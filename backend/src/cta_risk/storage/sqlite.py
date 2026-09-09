"""SQLite 账本适配层：同步方法仅在专用线程调用，不包含核算规则。"""

import fcntl
import hashlib
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import IO

from cta_risk.application.contracts import (
    ConfigurationMismatch,
    RecordedFill,
    RunDefinition,
    StorageError,
    StoredAccount,
    StoredLedger,
)
from cta_risk.domain.models import (
    Account,
    AccountBook,
    CloseAllocation,
    Exchange,
    Fill,
    Instrument,
    Offset,
    PositionLot,
    Side,
)


class SQLiteLedgerStore:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self._connection: sqlite3.Connection | None = None
        self._lock: IO[bytes] | None = None

    def _open(self) -> sqlite3.Connection:
        if self._connection is not None:
            raise StorageError("数据库连接已经打开")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            lock.close()
            raise StorageError("数据库已有账本进程占用") from exc
        self._lock = lock
        connection = sqlite3.connect(self.path, isolation_level=None)
        self._connection = connection
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        self._migrate(connection)
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, checksum TEXT NOT NULL)"
        )
        migrations = sorted((Path(__file__).resolve().parent / "migrations").glob("*.sql"))
        if not migrations:
            raise StorageError("缺少数据库迁移资源")
        applied = dict(connection.execute("SELECT name, checksum FROM schema_migrations"))
        if set(applied) - {item.name for item in migrations}:
            raise StorageError("数据库包含当前程序未知的迁移")
        for path in migrations:
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise StorageError("已应用的迁移内容发生变化")
                continue
            try:
                connection.executescript("BEGIN IMMEDIATE;\n" + sql)
                connection.execute(
                    "INSERT INTO schema_migrations VALUES (?, ?)", (path.name, checksum)
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def initialize(self, definition: RunDefinition, seed: StoredLedger) -> StoredLedger:
        try:
            connection = self._open()
            existing = connection.execute("SELECT * FROM runs").fetchall()
            if existing:
                if (
                    len(existing) != 1
                    or existing[0]["run_id"] != definition.run_id
                    or existing[0]["config_hash"] != definition.fingerprint
                    or existing[0]["config_json"] != definition.canonical_json()
                    or existing[0]["trading_day"] != str(definition.trading_day)
                ):
                    raise ConfigurationMismatch(
                        "数据库运行标识或业务配置不一致，请恢复原配置或使用新数据库"
                    )
                self._verify_catalog(definition)
                return self._load(definition.run_id)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?)",
                    (
                        definition.run_id,
                        str(definition.trading_day),
                        definition.fingerprint,
                        definition.canonical_json(),
                    ),
                )
                for instrument in definition.instruments:
                    connection.execute(
                        "INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            definition.run_id,
                            instrument.instrument_id,
                            instrument.product,
                            instrument.exchange.value,
                            instrument.multiplier,
                            str(instrument.tick_size),
                            str(instrument.margin_rate),
                            str(instrument.fee_per_lot),
                        ),
                    )
                for stored in seed.accounts:
                    book = stored.book
                    connection.execute(
                        "INSERT INTO accounts VALUES (?, ?, ?)",
                        (
                            definition.run_id,
                            book.account.account_id,
                            str(book.account.initial_capital),
                        ),
                    )
                    connection.execute(
                        "INSERT INTO account_books VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            definition.run_id,
                            book.account.account_id,
                            str(book.trading_day),
                            str(book.opening_balance),
                            str(book.realized_pnl),
                            str(book.fees),
                            stored.revision,
                        ),
                    )
                for record in seed.records:
                    self._insert_trade(definition.run_id, record)
                for stored in seed.accounts:
                    self._replace_positions(definition.run_id, stored.book)
                    self._insert_allocations(definition.run_id, stored.book, None)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            return self._load(definition.run_id)
        except (sqlite3.Error, OSError) as exc:
            raise StorageError("无法初始化或恢复账本存储") from exc

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("数据库尚未打开")
        return self._connection

    def _verify_catalog(self, definition: RunDefinition) -> None:
        """包括尚无成交的账户/合约，均应与冻结配置一致。"""
        accounts = {
            tuple(row)
            for row in self._db().execute(
                "SELECT account_id, initial_capital FROM accounts WHERE run_id=?",
                (definition.run_id,),
            )
        }
        instruments = {
            tuple(row)
            for row in self._db().execute(
                "SELECT instrument_id, product, exchange, multiplier, tick_size, "
                "margin_rate, fee_per_lot FROM instruments WHERE run_id=?",
                (definition.run_id,),
            )
        }
        if accounts != {
            (item.account_id, str(item.initial_capital)) for item in definition.accounts
        } or instruments != {
            (
                item.instrument_id,
                item.product,
                item.exchange.value,
                item.multiplier,
                str(item.tick_size),
                str(item.margin_rate),
                str(item.fee_per_lot),
            )
            for item in definition.instruments
        }:
            raise StorageError("账户或合约目录与冻结配置不一致，拒绝恢复")

    def _insert_trade(self, run_id: str, record: RecordedFill) -> None:
        fill = record.fill
        if record.fee is None:
            raise StorageError("成交记录缺少已核算手续费")
        self._db().execute(
            "INSERT INTO trades (run_id, account_id, fill_id, instrument_id, trading_day, "
            "sequence, side, offset, quantity, price, fee, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                fill.account_id,
                fill.fill_id,
                fill.instrument_id,
                str(fill.trading_day),
                fill.sequence,
                fill.side.value,
                fill.offset.value,
                fill.quantity,
                str(fill.price),
                str(record.fee),
                record.source,
            ),
        )

    def _replace_positions(self, run_id: str, book: AccountBook) -> None:
        connection = self._db()
        connection.execute(
            "DELETE FROM position_lots WHERE run_id=? AND account_id=?",
            (run_id, book.account.account_id),
        )
        for lot in book.lots:
            connection.execute(
                "INSERT INTO position_lots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    book.account.account_id,
                    lot.lot_id,
                    lot.instrument_id,
                    lot.side.value,
                    str(lot.opened_day),
                    lot.opened_sequence,
                    lot.quantity,
                    str(lot.open_price),
                    str(lot.basis),
                ),
            )

    def _insert_allocations(self, run_id: str, book: AccountBook, fill_id: str | None) -> None:
        for item in book.close_allocations:
            if fill_id is not None and item.fill_id != fill_id:
                continue
            self._db().execute(
                "INSERT INTO close_allocations "
                "(run_id, account_id, fill_id, lot_id, quantity, basis, close_price, pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    book.account.account_id,
                    item.fill_id,
                    item.lot_id,
                    item.quantity,
                    str(item.basis),
                    str(item.close_price),
                    str(item.pnl),
                ),
            )

    def save_fill(
        self, run_id: str, record: RecordedFill, book: AccountBook, expected_revision: int
    ) -> int:
        connection = self._db()
        revision = expected_revision + 1
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE account_books SET realized_pnl=?, fees=?, revision=? "
                "WHERE run_id=? AND account_id=? AND trading_day=? AND revision=?",
                (
                    str(book.realized_pnl),
                    str(book.fees),
                    revision,
                    run_id,
                    book.account.account_id,
                    str(book.trading_day),
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise StorageError("账本版本冲突，禁止覆盖其他写入")
            self._insert_trade(run_id, record)
            self._replace_positions(run_id, book)
            self._insert_allocations(run_id, book, record.fill.fill_id)
            connection.commit()
            return revision
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise StorageError("成交事务失败，已回滚") from exc
            raise

    def _load(self, run_id: str) -> StoredLedger:
        connection = self._db()
        instruments = {
            row["instrument_id"]: Instrument(
                row["instrument_id"],
                row["product"],
                Exchange(row["exchange"]),
                row["multiplier"],
                Decimal(row["tick_size"]),
                Decimal(row["margin_rate"]),
                Decimal(row["fee_per_lot"]),
            )
            for row in connection.execute("SELECT * FROM instruments WHERE run_id=?", (run_id,))
        }
        records = tuple(
            RecordedFill(
                Fill(
                    row["fill_id"],
                    row["account_id"],
                    row["instrument_id"],
                    date.fromisoformat(row["trading_day"]),
                    row["sequence"],
                    Side(row["side"]),
                    Offset(row["offset"]),
                    row["quantity"],
                    Decimal(row["price"]),
                ),
                row["source"],
                Decimal(row["fee"]),
            )
            for row in connection.execute(
                "SELECT * FROM trades WHERE run_id=? ORDER BY ingest_id", (run_id,)
            )
        )
        accounts: list[StoredAccount] = []
        rows = connection.execute(
            "SELECT b.*, a.initial_capital FROM account_books b "
            "JOIN accounts a USING(run_id, account_id) WHERE b.run_id=? ORDER BY b.account_id",
            (run_id,),
        ).fetchall()
        for row in rows:
            account_id = row["account_id"]
            fills = tuple(record.fill for record in records if record.fill.account_id == account_id)
            used = dict.fromkeys(fill.instrument_id for fill in fills)
            lots = tuple(
                PositionLot(
                    item["lot_id"],
                    item["instrument_id"],
                    Side(item["side"]),
                    date.fromisoformat(item["opened_day"]),
                    item["opened_sequence"],
                    item["quantity"],
                    Decimal(item["open_price"]),
                    Decimal(item["basis"]),
                )
                for item in connection.execute(
                    "SELECT * FROM position_lots WHERE run_id=? AND account_id=? "
                    "ORDER BY opened_day, opened_sequence, lot_id",
                    (run_id, account_id),
                )
            )
            allocations = tuple(
                CloseAllocation(
                    item["fill_id"],
                    item["lot_id"],
                    item["quantity"],
                    Decimal(item["basis"]),
                    Decimal(item["close_price"]),
                    Decimal(item["pnl"]),
                )
                for item in connection.execute(
                    "SELECT * FROM close_allocations WHERE run_id=? AND account_id=? "
                    "ORDER BY allocation_id",
                    (run_id, account_id),
                )
            )
            accounts.append(
                StoredAccount(
                    AccountBook(
                        Account(account_id, Decimal(row["initial_capital"])),
                        date.fromisoformat(row["trading_day"]),
                        Decimal(row["opening_balance"]),
                        tuple(instruments[key] for key in used),
                        lots,
                        fills,
                        allocations,
                        Decimal(row["realized_pnl"]),
                        Decimal(row["fees"]),
                    ),
                    row["revision"],
                )
            )
        return StoredLedger(tuple(accounts), records)

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
        finally:
            self._connection = None
            if self._lock is not None:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                self._lock = None
