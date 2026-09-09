"""POS-01 至 POS-04：单写者事务编排和不可变查询快照。"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TypeVar

from cta_risk.application.contracts import (
    AccountNotFound,
    FillResult,
    LedgerBusy,
    LedgerStore,
    LedgerUnavailable,
    RecordedFill,
    RunDefinition,
    StorageError,
    StoredAccount,
    StoredLedger,
)
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import Fill, PositionSummary, identifier, new_book
from cta_risk.domain.numbers import financial_context
from cta_risk.position.book import apply_fill
from cta_risk.position.query import PositionAggregate, aggregate_positions, query_positions

T = TypeVar("T")
logger = logging.getLogger(__name__)


def seed_ledger(definition: RunDefinition) -> StoredLedger:
    """初始成交只用于首次建库，在进入存储事务前完成完整校验。"""
    identifier(definition.run_id)
    if not definition.accounts or not definition.instruments:
        raise AccountingError("账户和合约配置不能为空")
    books = {
        account.account_id: new_book(account, definition.trading_day)
        for account in definition.accounts
    }
    instruments = {item.instrument_id: item for item in definition.instruments}
    if len(books) != len(definition.accounts) or len(instruments) != len(definition.instruments):
        raise AccountingError("账户或合约标识重复")
    records: list[RecordedFill] = []
    for record in definition.initial_fills:
        identifier(record.source)
        fill = record.fill
        if fill.account_id not in books or fill.instrument_id not in instruments:
            raise AccountingError("初始成交引用了未配置的账户或合约")
        previous = books[fill.account_id]
        updated = apply_fill(previous, fill, instruments[fill.instrument_id])
        if updated is previous:
            raise AccountingError("初始成交配置中存在重复记录")
        books[fill.account_id] = updated
        with financial_context():
            fee = updated.fees - previous.fees
        if record.fee is not None and record.fee != fee:
            raise AccountingError("成交手续费与核算规则不一致")
        records.append(replace(record, fee=fee))
    return StoredLedger(
        tuple(StoredAccount(book, len(book.fills)) for book in books.values()), tuple(records)
    )


@dataclass(frozen=True)
class _Ingest:
    record: RecordedFill
    response: asyncio.Future[FillResult]


class LedgerService:
    def __init__(self, definition: RunDefinition, store: LedgerStore, queue_capacity: int = 256):
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("队列容量必须为正整数")
        self.definition = definition
        self._store = store
        self._queue: asyncio.Queue[_Ingest | None] = asyncio.Queue(queue_capacity)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cta-ledger-db")
        self._task: asyncio.Task[None] | None = None
        self._accounts: MappingProxyType[str, StoredAccount] = MappingProxyType({})
        self._records: tuple[RecordedFill, ...] = ()
        self._healthy = False
        self._closing = False
        self._closed = False

    @property
    def available(self) -> bool:
        return self._healthy and not self._closing

    async def _database(self, operation: Callable[[], T]) -> T:
        # Only startup, the single consumer, and shutdown submit work, at most one at a time.
        return await asyncio.get_running_loop().run_in_executor(self._executor, operation)

    async def start(self) -> None:
        if self._task is not None or self._closed:
            raise LedgerUnavailable("账本服务已启动或关闭")
        try:
            seed = seed_ledger(self.definition)
            stored = await self._database(lambda: self._store.initialize(self.definition, seed))
            self._verify_recovery(stored)
            self._accounts = MappingProxyType(
                {item.book.account.account_id: item for item in stored.accounts}
            )
            self._records = stored.records
            self._healthy = True
            self._task = asyncio.create_task(self._consume(), name="cta-ledger-writer")
        except BaseException:
            await self.close()
            raise

    def _verify_recovery(self, stored: StoredLedger) -> None:
        """恢复时以成交事实重算核对持仓与余额，防止带着不一致快照运行。"""
        seed = seed_ledger(self.definition)
        if stored.records[: len(seed.records)] != seed.records:
            raise StorageError("初始成交与配置不一致，拒绝恢复")
        replayed = seed_ledger(
            RunDefinition(
                self.definition.run_id,
                self.definition.trading_day,
                self.definition.accounts,
                self.definition.instruments,
                stored.records,
            )
        )
        expected = {item.book.account.account_id: item for item in replayed.accounts}
        actual = {item.book.account.account_id: item for item in stored.accounts}
        if actual != expected:
            raise StorageError("持久化快照与成交记录不一致，拒绝恢复")

    def _ensure_available(self) -> None:
        if not self.available:
            raise LedgerUnavailable("账本服务未就绪或存储不可用")

    def accounts(self) -> tuple[StoredAccount, ...]:
        self._ensure_available()
        return tuple(self._accounts[key] for key in sorted(self._accounts))

    def account(self, account_id: str) -> StoredAccount:
        self._ensure_available()
        try:
            return self._accounts[account_id]
        except KeyError as exc:
            raise AccountNotFound(account_id) from exc

    def positions(
        self, account_id: str | None = None, product_id: str | None = None
    ) -> tuple[PositionSummary, ...]:
        if account_id is not None:
            self.account(account_id)
        return query_positions(
            (item.book for item in self.accounts()),
            account_id=account_id,
            product_id=product_id,
        )

    def position_summary(self, product_id: str | None = None) -> tuple[PositionAggregate, ...]:
        return aggregate_positions(self.positions(product_id=product_id))

    def trades(
        self, account_id: str | None = None, product_id: str | None = None
    ) -> tuple[RecordedFill, ...]:
        self._ensure_available()
        if account_id is not None:
            self.account(account_id)
        instruments = {item.instrument_id: item for item in self.definition.instruments}
        return tuple(
            record
            for record in self._records
            if (account_id is None or record.fill.account_id == account_id)
            and (
                product_id is None
                or instruments[record.fill.instrument_id].product_id == product_id
            )
        )

    async def record_fill(self, fill: Fill, source: str) -> FillResult:
        """内部已成交入口，供执行适配层调用；不是用户下单接口。"""
        self._ensure_available()
        identifier(source)
        response: asyncio.Future[FillResult] = asyncio.get_running_loop().create_future()
        # Observe exceptions even if the HTTP/adapter caller has disconnected.
        response.add_done_callback(
            lambda result: None if result.cancelled() else result.exception()
        )
        try:
            self._queue.put_nowait(_Ingest(RecordedFill(fill, source), response))
        except asyncio.QueueFull as exc:
            response.cancel()
            raise LedgerBusy("账本写入队列已满，成交尚未接收，请使用相同编号重试") from exc
        return await asyncio.shield(response)

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                try:
                    result = await self._apply(item.record)
                    item.response.set_result(result)
                except (AccountingError, AccountNotFound, LedgerUnavailable) as exc:
                    item.response.set_exception(exc)
                    logger.warning(
                        "成交未入账 account=%s fill=%s reason=%s",
                        item.record.fill.account_id,
                        item.record.fill.fill_id,
                        exc,
                    )
                except Exception as exc:
                    self._healthy = False
                    item.response.set_exception(
                        LedgerUnavailable("账本写入失败；停止写入，重启后核对恢复")
                    )
                    logger.exception(
                        "账本写入失败 account=%s fill=%s",
                        item.record.fill.account_id,
                        item.record.fill.fill_id,
                        exc_info=exc,
                    )
            finally:
                self._queue.task_done()

    async def _apply(self, record: RecordedFill) -> FillResult:
        # Closing drains accepted commands, but storage failure rejects subsequent commands.
        if not self._healthy:
            raise LedgerUnavailable("存储不可用")
        fill = record.fill
        current = self._accounts.get(fill.account_id)
        if current is None:
            raise AccountNotFound(fill.account_id)
        instrument = next(
            (
                item
                for item in self.definition.instruments
                if item.instrument_id == fill.instrument_id
            ),
            None,
        )
        if instrument is None:
            raise AccountingError("成交引用了未配置合约")
        updated = apply_fill(current.book, fill, instrument)
        if updated is current.book:
            return FillResult(fill.fill_id, fill.account_id, current.revision, True)
        with financial_context():
            record = replace(record, fee=updated.fees - current.book.fees)
        revision = await self._database(
            lambda: self._store.save_fill(
                self.definition.run_id,
                record,
                updated,
                current.revision,
            )
        )
        accounts = dict(self._accounts)
        accounts[fill.account_id] = StoredAccount(updated, revision)
        self._accounts = MappingProxyType(accounts)
        self._records = (*self._records, record)
        return FillResult(fill.fill_id, fill.account_id, revision, False)

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        if self._task is not None:
            await self._queue.put(None)
            await self._task
        try:
            await self._database(self._store.close)
        finally:
            self._executor.shutdown(wait=True)
            self._healthy = False
            self._closed = True
