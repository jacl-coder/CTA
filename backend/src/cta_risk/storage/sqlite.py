"""SQLite 账本适配层：同步方法仅在专用线程调用，不包含核算规则。"""

import fcntl
import hashlib
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Any

from cta_risk.application.contracts import (
    ConfigurationMismatch,
    JournalEntry,
    RecordedFill,
    RunDefinition,
    StorageError,
    StoredAccount,
    StoredLedger,
)
from cta_risk.application.market import EventObservation, StoredMarket
from cta_risk.domain.market import SourceFrame
from cta_risk.domain.models import (
    Account,
    AccountBook,
    CloseAllocation,
    DaySettlement,
    Exchange,
    Fill,
    Instrument,
    Offset,
    PositionLot,
    SettlementLine,
    Side,
)


def _decode_settlement(raw: dict[str, Any]) -> DaySettlement:
    """恢复报告中按 asdict 编码的结算值，不重新执行核算。"""
    try:
        return DaySettlement(
            account_id=raw["account_id"],
            trading_day=date.fromisoformat(raw["trading_day"]),
            prices=tuple((key, Decimal(value)) for key, value in raw["prices"]),
            lines=tuple(
                SettlementLine(
                    lot_id=item["lot_id"],
                    instrument_id=item["instrument_id"],
                    side=Side(item["side"]),
                    quantity=item["quantity"],
                    basis=Decimal(item["basis"]),
                    settlement_price=Decimal(item["settlement_price"]),
                    pnl=Decimal(item["pnl"]),
                )
                for item in raw["lines"]
            ),
            opening_balance=Decimal(raw["opening_balance"]),
            realized_pnl=Decimal(raw["realized_pnl"]),
            holding_pnl=Decimal(raw["holding_pnl"]),
            fees=Decimal(raw["fees"]),
            net_pnl=Decimal(raw["net_pnl"]),
            closing_balance=Decimal(raw["closing_balance"]),
            margin=Decimal(raw["margin"]),
            available=Decimal(raw["available"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise StorageError("日结报告中的结算记录损坏") from exc


def _report_payload(row: sqlite3.Row) -> str:
    payload: str = row["payload_json"]
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["payload_hash"]:
        raise StorageError("日结报告内容或哈希不一致")
    return payload


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
            self._write_fill(run_id, record, book, expected_revision)
            connection.commit()
            return revision
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise StorageError("成交事务失败，已回滚") from exc
            raise

    def _write_fill(
        self, run_id: str, record: RecordedFill, book: AccountBook, expected_revision: int
    ) -> None:
        connection = self._db()
        revision = expected_revision + 1
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

    def initialize_trading(self, run_id: str, policy_json: str | None) -> tuple[JournalEntry, ...]:
        connection = self._db()
        row = connection.execute(
            "SELECT policy_json, revision FROM trading_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is not None and row[0] != policy_json:
            raise ConfigurationMismatch("交易配置不一致；已有交易运行不能关闭风控或修改规则")
        if policy_json is None:
            return ()
        if row is None:
            connection.execute(
                "INSERT INTO trading_runs (run_id, policy_json) VALUES (?, ?)",
                (run_id, policy_json),
            )
        else:
            count, latest = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(revision), 0) FROM trading_journal WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if count != row[1] or latest != row[1]:
                raise StorageError("交易日志不完整，拒绝恢复")
        return tuple(
            JournalEntry(item[0], item[1], item[2])
            for item in connection.execute(
                "SELECT revision, command_json, outcome_json FROM trading_journal "
                "WHERE run_id=? ORDER BY revision",
                (run_id,),
            )
        )

    def read_journal(self, run_id: str, revision: int) -> tuple[JournalEntry, ...]:
        return tuple(
            JournalEntry(item[0], item[1], item[2])
            for item in self._db().execute(
                "SELECT revision, command_json, outcome_json FROM trading_journal "
                "WHERE run_id=? AND revision<=? ORDER BY revision",
                (run_id, revision),
            )
        )

    def _insert_journal(self, run_id: str, entry: JournalEntry) -> None:
        latest = (
            self._db()
            .execute(
                "SELECT COALESCE(MAX(revision), 0) FROM trading_journal WHERE run_id=?", (run_id,)
            )
            .fetchone()[0]
        )
        if entry.revision != latest + 1:
            raise StorageError("交易日志版本冲突")
        self._db().execute(
            "INSERT INTO trading_journal VALUES (?, ?, ?, ?)",
            (run_id, entry.revision, entry.command_json, entry.outcome_json),
        )
        updated = self._db().execute(
            "UPDATE trading_runs SET revision=? WHERE run_id=? AND revision=?",
            (entry.revision, run_id, entry.revision - 1),
        )
        if updated.rowcount != 1:
            raise StorageError("交易日志检查点版本冲突")

    def save_transition(
        self,
        run_id: str,
        entry: JournalEntry,
        record: RecordedFill | None,
        book: AccountBook | None,
        expected_revision: int | None,
        market_sequence: int | None = None,
        observations: tuple[EventObservation, ...] = (),
    ) -> None:
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if record is not None:
                if book is None or expected_revision is None:
                    raise StorageError("成交事务缺少账本或修订号")
                self._write_fill(run_id, record, book, expected_revision)
            self._insert_journal(run_id, entry)
            if market_sequence is not None:
                self._update_market_cursor(run_id, market_sequence, observations)
            connection.commit()
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise StorageError("交易与风险事务失败，已回滚") from exc
            raise

    def verify_allocations(
        self, run_id: str, allocations: tuple[tuple[str, CloseAllocation], ...]
    ) -> None:
        actual = tuple(
            (
                row["account_id"],
                CloseAllocation(
                    row["fill_id"],
                    row["lot_id"],
                    row["quantity"],
                    Decimal(row["basis"]),
                    Decimal(row["close_price"]),
                    Decimal(row["pnl"]),
                ),
            )
            for row in self._db().execute(
                "SELECT * FROM close_allocations WHERE run_id=?", (run_id,)
            )
        )
        if len(actual) != len(allocations) or set(actual) != set(allocations):
            raise StorageError("历史平仓分摊与完整交易日志重放不一致")

    def initialize_settlement(self, run_id: str, config_json: str | None) -> tuple[str, ...]:
        connection = self._db()
        try:
            row = connection.execute(
                "SELECT config_json FROM settlement_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is not None and row["config_json"] != config_json:
                raise ConfigurationMismatch("日结配置不一致；已有日结运行不能关闭或修改配置")
            if config_json is None:
                return ()
            if row is None:
                connection.execute(
                    "INSERT INTO settlement_runs (run_id, config_json) VALUES (?, ?)",
                    (run_id, config_json),
                )
            return tuple(
                _report_payload(item)
                for item in connection.execute(
                    "SELECT payload_json, payload_hash FROM daily_reports "
                    "WHERE run_id=? ORDER BY trading_day",
                    (run_id,),
                )
            )
        except sqlite3.Error as exc:
            raise StorageError("无法初始化或恢复日结存储") from exc

    def save_day_transition(
        self,
        run_id: str,
        entry: JournalEntry,
        accounts: tuple[StoredAccount, ...],
        report_json: str | None,
    ) -> None:
        """全账户日状态、持仓、日志及可选冻结报告共同提交。"""
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            checkpoint = connection.execute(
                "SELECT revision FROM trading_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if checkpoint is None or checkpoint["revision"] != entry.revision - 1:
                raise StorageError("交易日志检查点版本冲突")
            account_ids = {stored.book.account.account_id for stored in accounts}
            expected_ids = {
                row["account_id"]
                for row in connection.execute(
                    "SELECT account_id FROM accounts WHERE run_id=?", (run_id,)
                )
            }
            if not accounts or len(account_ids) != len(accounts) or account_ids != expected_ids:
                raise StorageError("日结事务必须包含全部账户且不能重复")
            if len({stored.book.trading_day for stored in accounts}) != 1:
                raise StorageError("日结事务中的账户交易日不一致")
            for stored in accounts:
                book = stored.book
                if type(stored.revision) is not int or stored.revision < 0:
                    raise StorageError("账本修订号非法")
                updated = connection.execute(
                    "UPDATE account_books SET trading_day=?, opening_balance=?, "
                    "realized_pnl=?, fees=?, revision=? "
                    "WHERE run_id=? AND account_id=? AND revision<=?",
                    (
                        str(book.trading_day),
                        str(book.opening_balance),
                        str(book.realized_pnl),
                        str(book.fees),
                        stored.revision,
                        run_id,
                        book.account.account_id,
                        stored.revision,
                    ),
                )
                if updated.rowcount != 1:
                    raise StorageError("账本版本冲突，禁止覆盖其他写入")
                self._replace_positions(run_id, book)
            self._insert_journal(run_id, entry)
            if report_json is not None:
                try:
                    report_day = json.loads(report_json)["trading_day"]
                    if date.fromisoformat(report_day).isoformat() != report_day:
                        raise ValueError("非标准交易日")
                except (KeyError, TypeError, ValueError) as exc:
                    raise StorageError("日结报告缺少合法交易日") from exc
                connection.execute(
                    "INSERT INTO daily_reports "
                    "(run_id, trading_day, payload_json, payload_hash) VALUES (?, ?, ?, ?)",
                    (
                        run_id,
                        report_day,
                        report_json,
                        hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
                    ),
                )
            connection.commit()
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise StorageError("日结事务失败，已回滚") from exc
            raise

    def _update_market_cursor(
        self, run_id: str, sequence: int, observations: tuple[EventObservation, ...]
    ) -> None:
        updated = self._db().execute(
            "UPDATE market_runs SET applied_sequence=? WHERE run_id=? AND applied_sequence=?",
            (sequence, run_id, sequence - 1),
        )
        if updated.rowcount != 1:
            raise StorageError("行情应用游标冲突")
        for item in observations:
            self._db().execute(
                "INSERT INTO market_event_observations VALUES (?, ?, ?, ?, ?)",
                (run_id, item.event_id, item.occurred_ms, item.detected_ms, int(item.recovered)),
            )

    def initialize_market(
        self, run_id: str, plan_json: str | None, epoch_ms: int
    ) -> StoredMarket | None:
        connection = self._db()
        row = connection.execute("SELECT * FROM market_runs WHERE run_id=?", (run_id,)).fetchone()
        if row is not None and row["plan_json"] != plan_json:
            raise ConfigurationMismatch("已有多源运行不能修改场景或关闭行情接入")
        if plan_json is None:
            return None
        if row is None:
            connection.execute(
                "INSERT INTO market_runs (run_id, plan_json, epoch_ms) VALUES (?, ?, ?)",
                (run_id, plan_json, epoch_ms),
            )
            return StoredMarket(epoch_ms, 0)
        frames = []
        for item in connection.execute(
            "SELECT * FROM market_frames WHERE run_id=? ORDER BY source_id, sequence", (run_id,)
        ):
            frame = SourceFrame.from_json(item["payload_json"])
            if (
                frame.source_id != item["source_id"]
                or frame.sequence != item["sequence"]
                or frame.digest != item["digest"]
            ):
                raise StorageError("持久化行情内容或哈希不一致")
            frames.append(frame)
        observations = tuple(
            EventObservation(
                item["event_id"], item["occurred_ms"], item["detected_ms"], bool(item["recovered"])
            )
            for item in connection.execute(
                "SELECT * FROM market_event_observations WHERE run_id=? ORDER BY event_id",
                (run_id,),
            )
        )
        return StoredMarket(row["epoch_ms"], row["applied_sequence"], tuple(frames), observations)

    def save_source_frames(self, run_id: str, frames: tuple[SourceFrame, ...]) -> None:
        connection = self._db()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for frame in frames:
                previous = connection.execute(
                    "SELECT payload_json FROM market_frames "
                    "WHERE run_id=? AND source_id=? AND sequence=?",
                    (run_id, frame.source_id, frame.sequence),
                ).fetchone()
                if previous is not None:
                    if previous[0] != frame.payload():
                        raise StorageError("同源同序号行情冲突")
                    continue
                connection.execute(
                    "INSERT INTO market_frames VALUES (?, ?, ?, ?, ?)",
                    (run_id, frame.source_id, frame.sequence, frame.payload(), frame.digest),
                )
            connection.commit()
        except BaseException as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise StorageError("原始行情事务失败，已回滚") from exc
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
        settlements: dict[tuple[str, str], DaySettlement] = {}
        report_days: set[str] = set()
        for report in connection.execute(
            "SELECT trading_day, payload_json, payload_hash FROM daily_reports "
            "WHERE run_id=? AND trading_day IN "
            "(SELECT trading_day FROM account_books WHERE run_id=?)",
            (run_id, run_id),
        ):
            payload = _report_payload(report)
            try:
                decoded = json.loads(payload)
                if decoded["trading_day"] != report["trading_day"]:
                    raise StorageError("日结报告交易日与存储记录不一致")
                report_days.add(report["trading_day"])
                for item in decoded["accounts"]:
                    settlement = _decode_settlement(item["settlement"])
                    key = str(settlement.trading_day), settlement.account_id
                    if key[0] != report["trading_day"] or key in settlements:
                        raise StorageError("日结报告中的账户结算重复或交易日不一致")
                    settlements[key] = settlement
            except (KeyError, TypeError, ValueError) as exc:
                raise StorageError("日结报告结构损坏") from exc
        for row in rows:
            account_id = row["account_id"]
            current_settlement = settlements.get((row["trading_day"], account_id))
            if row["trading_day"] in report_days and current_settlement is None:
                raise StorageError("日结报告缺少账户结算记录")
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
                    "SELECT c.* FROM close_allocations c "
                    "JOIN trades t ON t.run_id=c.run_id AND t.account_id=c.account_id "
                    "AND t.fill_id=c.fill_id "
                    "WHERE c.run_id=? AND c.account_id=? AND t.trading_day=? "
                    "ORDER BY c.allocation_id",
                    (run_id, account_id, row["trading_day"]),
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
                        current_settlement,
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
