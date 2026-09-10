"""POS-01 至 POS-04：单写者事务编排和不可变查询快照。"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TypeVar

from cta_risk.application.contracts import (
    AccountNotFound,
    FillResult,
    JournalEntry,
    LedgerBusy,
    LedgerStore,
    LedgerUnavailable,
    RecordedFill,
    RunDefinition,
    StorageError,
    StoredAccount,
    StoredLedger,
)
from cta_risk.application.market import (
    EventObservation,
    MarketCommand,
    MarketGate,
    SourceBatch,
    SourceStatus,
    StoredMarket,
    assemble,
    contiguous,
    validate_frame,
)
from cta_risk.application.settlement import DayTransition, advance_day
from cta_risk.application.trading import (
    FrameResult,
    OrderCommand,
    OrderResult,
    TradingState,
    Transition,
    advance,
    decode_command,
    encode,
    initial_state,
)
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.market import MarketPlan, SourceFrame
from cta_risk.domain.models import CloseAllocation, Fill, PositionSummary, identifier, new_book
from cta_risk.domain.numbers import financial_context
from cta_risk.domain.settlement import DayCommand, DayResult, SettlementPlan
from cta_risk.domain.trading import Order, PriceFrame, RiskPolicy
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


@dataclass(frozen=True)
class _TradingIngest:
    command: Order | PriceFrame
    received_at: float
    response: asyncio.Future[OrderResult | FrameResult]


@dataclass(frozen=True)
class _MarketIngest:
    command: MarketCommand
    response: asyncio.Future[None]


@dataclass(frozen=True)
class _DayIngest:
    command: DayCommand
    response: asyncio.Future[DayResult]


class LedgerService:
    def __init__(
        self,
        definition: RunDefinition,
        store: LedgerStore,
        queue_capacity: int = 256,
        *,
        policy: RiskPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        market_plan: MarketPlan | None = None,
        wall_clock: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        market_epoch_ms: int | None = None,
        settlement_plan: SettlementPlan | None = None,
    ):
        if type(queue_capacity) is not int or queue_capacity < 1:
            raise ValueError("队列容量必须为正整数")
        self.definition = definition
        self._store = store
        self.policy = policy
        self._clock = clock
        if market_plan is not None and policy is None:
            raise ValueError("多源行情必须启用交易风控")
        self.market_plan = market_plan
        self.settlement_plan = settlement_plan
        if settlement_plan is not None and policy is None:
            raise ValueError("日结需要启用交易日志")
        self._wall_clock = wall_clock
        self._market_epoch_ms = market_epoch_ms
        self._market: StoredMarket | None = None
        self._market_synced = False
        self._market_reason = "等待连接行情源"
        self._source_status = (
            tuple(SourceStatus(item.source_id) for item in market_plan.sources)
            if market_plan
            else ()
        )
        self._duplicate_frames = 0
        self._trading: TradingState | None = None
        self._frame_received_at: float | None = None
        self._market_live = False
        self._queue: asyncio.Queue[_Ingest | _TradingIngest | _MarketIngest | _DayIngest | None] = (
            asyncio.Queue(queue_capacity)
        )
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
            if self.policy is None:
                self._verify_recovery(stored)
            elif stored.records[: len(seed.records)] != seed.records:
                raise StorageError("初始成交与冻结配置不一致")
            journal = await self._database(
                lambda: self._store.initialize_trading(
                    self.definition.run_id, encode(self.policy) if self.policy is not None else None
                )
            )
            observed_event_ids: set[int] = set()
            reports = await self._database(
                lambda: self._store.initialize_settlement(
                    self.definition.run_id,
                    encode(self.settlement_plan) if self.settlement_plan else None,
                )
            )
            if self.policy is not None:
                state = initial_state(seed)
                allocations: list[tuple[str, CloseAllocation]] = []
                try:
                    for entry in journal:
                        decoded = decode_command(entry.command_json)
                        transition: Transition | DayTransition
                        if isinstance(decoded, DayCommand):
                            if decoded.action == "open_day":
                                allocations.extend(
                                    (item.book.account.account_id, allocation)
                                    for item in state.ledger.accounts
                                    for allocation in item.book.close_allocations
                                )
                            if self.settlement_plan is None:
                                raise StorageError("日结日志缺少配置")
                            transition = advance_day(
                                state, decoded, self.definition, self.policy, self.settlement_plan
                            )
                        else:
                            transition = advance(
                                state, decoded, self.definition, self.policy, self.settlement_plan
                            )
                        if isinstance(decoded, PriceFrame) and decoded.source == "multi-source":
                            observed_event_ids.update(
                                event.event_id
                                for event in transition.state.events[len(state.events) :]
                            )
                        if (
                            transition.result.duplicate
                            or transition.state.revision != entry.revision
                            or transition.command_json != entry.command_json
                            or transition.outcome_json != entry.outcome_json
                        ):
                            raise StorageError("交易日志重放结果不一致")
                        state = transition.state
                    if {item.book.account.account_id: item for item in state.ledger.accounts} != {
                        item.book.account.account_id: item for item in stored.accounts
                    } or state.ledger.records != stored.records:
                        raise StorageError("交易日志与成交账本不一致；启用交易需使用初始化账本")
                    if state.reports != reports:
                        raise StorageError("清算报告与日结日志重放不一致")
                    allocations.extend(
                        (item.book.account.account_id, allocation)
                        for item in state.ledger.accounts
                        for allocation in item.book.close_allocations
                    )
                    await self._database(
                        lambda: self._store.verify_allocations(
                            self.definition.run_id, tuple(allocations)
                        )
                    )
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    raise StorageError("交易日志损坏，拒绝恢复") from exc
                self._trading = state
            market = await self._database(
                lambda: self._store.initialize_market(
                    self.definition.run_id,
                    self.market_plan.to_json() if self.market_plan else None,
                    self._market_epoch_ms
                    if self._market_epoch_ms is not None
                    else self._wall_clock() + 1000,
                )
            )
            if market is not None and self.market_plan is not None:
                try:
                    for frame in market.frames:
                        validate_frame(self.market_plan, market.epoch_ms, frame)
                    if (
                        self._trading is None
                        or len(self._trading.frames) != market.applied_sequence
                    ):
                        raise StorageError("行情应用游标与交易日志不一致")
                    for applied_frame in self._trading.frames:
                        if (
                            assemble(self.market_plan, market, applied_frame.sequence)
                            != applied_frame
                        ):
                            raise StorageError("原始行情与已应用帧不一致")
                    if {item.event_id for item in market.observations} != observed_event_ids:
                        raise StorageError("行情风险事件追踪记录缺失或重复")
                    events = {event.event_id: event for event in self._trading.events}
                    for item in market.observations:
                        event = events.get(item.event_id)
                        if (
                            event is None
                            or item.occurred_ms
                            != market.epoch_ms
                            + (event.frame_sequence - 1) * self.market_plan.interval_ms
                            or item.detected_ms < item.occurred_ms
                        ):
                            raise StorageError("行情风险事件追踪信息不一致")
                except (ValueError, TypeError, KeyError) as exc:
                    raise StorageError("原始行情恢复校验失败") from exc
                self._market = market
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
        if self.policy is not None:
            raise LedgerUnavailable("交易运行必须经过下单风控，禁止直接导入成交")
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
                    if isinstance(item, _DayIngest):
                        item.response.set_result(await self._process_day(item.command))
                    elif isinstance(item, _MarketIngest):
                        await self._process_market(item.command)
                        item.response.set_result(None)
                    elif isinstance(item, _TradingIngest):
                        item.response.set_result(await self._process_trading(item))
                    else:
                        item.response.set_result(await self._apply(item.record))
                except (AccountingError, AccountNotFound, LedgerUnavailable) as exc:
                    if isinstance(item, _MarketIngest):
                        self._market_synced = False
                        self._market_reason = "行情校验失败，暂停交易"
                    item.response.set_exception(exc)
                    logger.warning("业务命令未执行 reason=%s", exc)
                except Exception:
                    self._healthy = False
                    item.response.set_exception(
                        LedgerUnavailable("账本写入失败；停止写入，重启后核对恢复")
                    )
                    logger.exception("业务事务写入失败")
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

    @property
    def trading_enabled(self) -> bool:
        return self.policy is not None

    def _market_fresh(self) -> bool:
        return (
            self._healthy
            and self._day_tradable()
            and self._sources_current()
            and self._market_live
            and self.policy is not None
            and self._frame_received_at is not None
            and 0 <= self._clock() - self._frame_received_at <= self.policy.max_price_age_seconds
        )

    @property
    def trading_available(self) -> bool:
        return self.available and self._market_fresh()

    def trading_state(self) -> TradingState:
        self._ensure_available()
        if self._trading is None:
            raise LedgerUnavailable("未启用模拟交易")
        return self._trading

    async def _enqueue_trading(self, command: Order | PriceFrame) -> OrderResult | FrameResult:
        self.trading_state()
        response: asyncio.Future[OrderResult | FrameResult] = (
            asyncio.get_running_loop().create_future()
        )
        response.add_done_callback(
            lambda result: None if result.cancelled() else result.exception()
        )
        try:
            self._queue.put_nowait(_TradingIngest(command, self._clock(), response))
        except asyncio.QueueFull as exc:
            response.cancel()
            raise LedgerBusy("业务队列已满，命令尚未接收，请使用相同编号重试") from exc
        return await asyncio.shield(response)

    async def submit_order(self, order: Order) -> OrderResult:
        result = await self._enqueue_trading(order)
        assert isinstance(result, OrderResult)
        return result

    async def publish_frame(self, frame: PriceFrame) -> FrameResult:
        if self.market_plan is not None:
            raise LedgerUnavailable("多源模式禁止手工价格覆盖")
        result = await self._enqueue_trading(frame)
        assert isinstance(result, FrameResult)
        return result

    async def _process_trading(self, item: _TradingIngest) -> OrderResult | FrameResult:
        if not self._healthy or self._trading is None or self.policy is None:
            raise LedgerUnavailable("交易服务不可用")
        command = item.command
        if isinstance(command, PriceFrame):
            try:
                if self._clock() - item.received_at > self.policy.max_price_age_seconds:
                    raise AccountingError("行情在队列等待期间已过期")
                transition = advance(
                    self._trading, command, self.definition, self.policy, self.settlement_plan
                )
            except AccountingError:
                # Pause after a gap/conflict until the next valid frame arrives.
                self._market_live = False
                raise
        else:
            transition = advance(
                self._trading,
                OrderCommand(command, self._market_fresh()),
                self.definition,
                self.policy,
                self.settlement_plan,
            )
        if transition.result.duplicate:
            return transition.result
        await self._commit_trading(transition, item.received_at)
        return transition.result

    async def _commit_trading(
        self,
        transition: Transition,
        received_at: float,
        market_sequence: int | None = None,
        observations: tuple[EventObservation, ...] = (),
    ) -> None:
        entry = JournalEntry(
            transition.state.revision, transition.command_json, transition.outcome_json
        )
        await self._database(
            lambda: self._store.save_transition(
                self.definition.run_id,
                entry,
                transition.record,
                transition.book,
                transition.expected_revision,
                market_sequence,
                observations,
            )
        )
        self._trading = transition.state
        self._accounts = MappingProxyType(
            {item.book.account.account_id: item for item in transition.state.ledger.accounts}
        )
        self._records = transition.state.ledger.records
        if isinstance(transition.result, FrameResult):
            self._frame_received_at = received_at
            self._market_live = True

    def _sources_current(self) -> bool:
        if self.market_plan is None:
            return True
        return (
            self._market_synced
            and self._market is not None
            and all(item.connected for item in self._source_status)
            and self._market.applied_sequence
            == self.market_plan.sequence_at(self._market.epoch_ms, self._wall_clock())
            and not self.market_plan.completed(self._market.epoch_ms, self._wall_clock())
        )

    def source_state(self) -> StoredMarket:
        self._ensure_available()
        if self._market is None:
            raise LedgerUnavailable("未启用多源行情")
        return self._market

    def source_status(self) -> dict[str, object]:
        stored = self.source_state()
        assert self.market_plan is not None
        completed = self.market_plan.completed(stored.epoch_ms, self._wall_clock())
        current = self.market_plan.sequence_at(stored.epoch_ms, self._wall_clock())
        ready = self.trading_available
        return {
            "epoch_ms": stored.epoch_ms,
            "applied_sequence": stored.applied_sequence,
            "expected_sequence": current,
            "market_ready": ready,
            "completed": completed and stored.applied_sequence == self.market_plan.frame_count,
            "reason": ""
            if ready
            else (
                "演示场景已结束"
                if completed and stored.applied_sequence == current
                else self._market_reason or "等待下一完整行情帧"
            ),
            "duplicate_frames": self._duplicate_frames,
            "sources": [
                {
                    "source_id": item.source_id,
                    "connected": item.connected,
                    "head": item.head,
                    "contiguous_sequence": contiguous(stored.frames, item.source_id),
                    "received_count": sum(
                        frame.source_id == item.source_id for frame in stored.frames
                    ),
                    "reason": item.reason,
                }
                for item in self._source_status
            ],
        }

    async def market_command(self, command: MarketCommand) -> None:
        self.source_state()
        response: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        response.add_done_callback(
            lambda result: None if result.cancelled() else result.exception()
        )
        try:
            self._queue.put_nowait(_MarketIngest(command, response))
        except asyncio.QueueFull as exc:
            response.cancel()
            raise LedgerBusy("行情队列已满，尚未接收，请按原游标重试") from exc
        await asyncio.shield(response)

    async def _process_market(self, command: MarketCommand) -> None:
        if not self._healthy or self._market is None or self.market_plan is None:
            raise LedgerUnavailable("多源服务不可用")
        stored = self._market
        if isinstance(command, MarketGate):
            if {item.source_id for item in command.sources} != {
                item.source_id for item in self.market_plan.sources
            }:
                raise AccountingError("行情连接状态不完整")
            self._source_status = command.sources
            self._market_synced = command.ready
            self._market_reason = command.reason
        elif isinstance(command, SourceBatch):
            previous = {(item.source_id, item.sequence): item for item in stored.frames}
            added: list[SourceFrame] = []
            duplicate = 0
            for frame in command.frames:
                validate_frame(self.market_plan, stored.epoch_ms, frame)
                if frame.sequence > self.market_plan.sequence_at(
                    stored.epoch_ms, self._wall_clock()
                ):
                    raise AccountingError("拒绝尚未发生的未来行情")
                key = frame.source_id, frame.sequence
                if key in previous:
                    if previous[key] != frame:
                        raise AccountingError("同源同序号行情内容冲突")
                    duplicate += 1
                else:
                    added.append(frame)
                    previous[key] = frame
            if added:
                await self._database(
                    lambda: self._store.save_source_frames(self.definition.run_id, tuple(added))
                )
                self._market = replace(
                    stored,
                    frames=tuple(
                        sorted(previous.values(), key=lambda item: (item.source_id, item.sequence))
                    ),
                )
            self._duplicate_frames += duplicate
        else:
            self._market_synced = False
            assert self._trading is not None and self.policy is not None
            combined = assemble(self.market_plan, stored, stored.applied_sequence + 1)
            transition = advance(
                self._trading, combined, self.definition, self.policy, self.settlement_plan
            )
            detected = self._wall_clock()
            observations = tuple(
                EventObservation(
                    event.event_id,
                    stored.epoch_ms + (combined.sequence - 1) * self.market_plan.interval_ms,
                    detected,
                    command.recovered,
                )
                for event in transition.state.events[len(self._trading.events) :]
            )
            await self._commit_trading(transition, self._clock(), combined.sequence, observations)
            self._market = replace(
                stored,
                applied_sequence=combined.sequence,
                observations=(*stored.observations, *observations),
            )

    def _day_tradable(self) -> bool:
        state = self._trading
        if state is None or state.phase != "OPEN":
            return False
        if state.frame is None or state.frame.trading_day != state.trading_day:
            return False
        return not self.session_has_closed()

    def session_has_closed(self) -> bool:
        if self.settlement_plan and self.market_plan and self._market and self._trading:
            session = next(
                item.session
                for item in self.settlement_plan.days
                if item.session.trading_day == self._trading.trading_day
            )
            return self._wall_clock() >= self._market.epoch_ms + (
                session.close_sequence * self.market_plan.interval_ms
            )
        return False

    def market_frame_allowed(self, sequence: int) -> bool:
        if self.settlement_plan is None:
            return True
        state = self.trading_state()
        session = next(
            item.session
            for item in self.settlement_plan.days
            if item.session.trading_day == state.trading_day
        )
        return state.phase != "SETTLED" and sequence <= session.close_sequence

    def session_status(self) -> dict[str, object]:
        state = self.trading_state()
        if self.settlement_plan is None:
            raise LedgerUnavailable("未启用日结流程")
        day = next(
            item
            for item in self.settlement_plan.days
            if item.session.trading_day == state.trading_day
        )
        return {
            "trading_day": str(state.trading_day),
            "phase": state.phase,
            "close_sequence": day.session.close_sequence,
            "applied_sequence": len(state.frames),
            "market_ready": self.trading_available,
            "settled_days": [json.loads(report)["trading_day"] for report in state.reports],
        }

    async def day_command(self, command: DayCommand) -> DayResult:
        self.trading_state()
        if self.settlement_plan is None:
            raise LedgerUnavailable("未启用日结流程")
        response: asyncio.Future[DayResult] = asyncio.get_running_loop().create_future()
        response.add_done_callback(
            lambda result: None if result.cancelled() else result.exception()
        )
        try:
            self._queue.put_nowait(_DayIngest(command, response))
        except asyncio.QueueFull as exc:
            response.cancel()
            raise LedgerBusy("业务队列已满，日结命令尚未接收") from exc
        return await asyncio.shield(response)

    async def _process_day(self, command: DayCommand) -> DayResult:
        if not self._healthy or self._trading is None or self.policy is None:
            raise LedgerUnavailable("交易服务不可用")
        assert self.settlement_plan is not None
        if command.action == "settle" and self.market_plan is not None:
            day = next(
                (
                    item
                    for item in self.settlement_plan.days
                    if item.session.trading_day == command.trading_day
                ),
                None,
            )
            if day and self.source_state().applied_sequence < day.session.close_sequence:
                raise AccountingError("行情尚未补齐收盘帧，不能清算")
        transition = advance_day(
            self._trading, command, self.definition, self.policy, self.settlement_plan
        )
        if transition.result.duplicate:
            return transition.result
        await self._database(
            lambda: self._store.save_day_transition(
                self.definition.run_id,
                JournalEntry(
                    transition.state.revision, transition.command_json, transition.outcome_json
                ),
                transition.state.ledger.accounts,
                transition.report_json,
            )
        )
        self._trading = transition.state
        self._accounts = MappingProxyType(
            {item.book.account.account_id: item for item in transition.state.ledger.accounts}
        )
        if command.action == "open_day":
            self._market_live = False
        return transition.result

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
