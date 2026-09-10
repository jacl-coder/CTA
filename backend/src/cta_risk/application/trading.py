"""可重放的交易状态转换；不进行 I/O，应用队列负责提交后发布。"""

import json
from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Literal

from cta_risk.application.contracts import (
    AccountNotFound,
    RecordedFill,
    RunDefinition,
    StoredAccount,
    StoredLedger,
)
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, Offset, Side
from cta_risk.domain.numbers import financial_context
from cta_risk.domain.settlement import DayCommand, SettlementPlan
from cta_risk.domain.trading import CommandConflict, Order, PriceFrame, RiskPolicy
from cta_risk.execution.simulated import simulate_fill
from cta_risk.position.book import apply_fill
from cta_risk.risk.rules import RiskEvent, RiskState, evaluate, transitions
from cta_risk.valuation.calculator import value_account


def encode(value: object) -> str:
    def default(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            raw = asdict(item)
            if isinstance(item, OrderResult) and item.processed_at_ms is None:
                raw.pop("processed_at_ms")  # Keep pre-timestamp journal outcomes replayable.
            if isinstance(item, SettlementPlan) and not item.repeat_daily:
                raw.pop("repeat_daily")  # Preserve existing finite-run journal/report hashes.
            return raw
        if isinstance(item, (Decimal, date)):
            return str(item)
        raise TypeError(f"无法序列化 {type(item).__name__}")

    return json.dumps(value, default=default, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class OrderResult:
    request_id: str
    account_id: str
    status: Literal["FILLED", "REJECTED"]
    reason: str
    frame_sequence: int | None
    fill_id: str | None = None
    price: Decimal | None = None
    fee: Decimal | None = None
    duplicate: bool = False
    processed_at_ms: int | None = None


@dataclass(frozen=True)
class FrameResult:
    sequence: int
    duplicate: bool = False


@dataclass(frozen=True)
class OrderCommand:
    order: Order
    market_ready: bool
    processed_at_ms: int | None = None

    def __post_init__(self) -> None:
        if self.processed_at_ms is not None and (
            type(self.processed_at_ms) is not int or self.processed_at_ms < 0
        ):
            raise AccountingError("订单处理时间必须为非负毫秒时间戳")


Command = PriceFrame | OrderCommand | DayCommand


def decode_command(payload: str) -> Command:
    raw = json.loads(payload)
    if raw["kind"] in ("close", "settle", "open_day"):
        return DayCommand(
            raw["kind"],
            date.fromisoformat(raw["trading_day"]),
            tuple((key, Decimal(value)) for key, value in raw.get("prices", [])),
        )
    if raw["kind"] == "frame":
        return PriceFrame(
            raw["sequence"],
            date.fromisoformat(raw["trading_day"]),
            tuple((key, Decimal(value)) for key, value in raw["prices"]),
            raw["source"],
        )
    if raw["kind"] != "order" or type(raw["market_ready"]) is not bool:
        raise AccountingError("未知或不合法的交易日志命令")
    item = raw["order"]
    return OrderCommand(
        Order(
            item["request_id"],
            item["account_id"],
            item["instrument_id"],
            date.fromisoformat(item["trading_day"]),
            Side(item["side"]),
            Offset(item["offset"]),
            item["quantity"],
        ),
        raw["market_ready"],
        raw.get("processed_at_ms"),
    )


@dataclass(frozen=True)
class TradingState:
    ledger: StoredLedger
    frames: tuple[PriceFrame, ...]
    risks: tuple[RiskState, ...]
    events: tuple[RiskEvent, ...] = ()
    orders: tuple[tuple[Order, OrderResult], ...] = ()
    revision: int = 0
    phase: str = "OPEN"
    close_sequence: int | None = None
    reports: tuple[str, ...] = ()
    opened_days: tuple[date, ...] = ()

    @property
    def trading_day(self) -> date:
        return self.ledger.accounts[0].book.trading_day

    @property
    def frame(self) -> PriceFrame | None:
        return self.frames[-1] if self.frames else None


@dataclass(frozen=True)
class Transition:
    state: TradingState
    command_json: str
    outcome_json: str
    result: OrderResult | FrameResult
    record: RecordedFill | None = None
    book: AccountBook | None = None
    expected_revision: int | None = None


def initial_state(seed: StoredLedger) -> TradingState:
    return TradingState(
        seed, (), tuple(RiskState(item.book.account.account_id) for item in seed.accounts)
    )


def _refresh(state: TradingState, policy: RiskPolicy) -> TradingState:
    assert state.frame is not None
    prices = dict(state.frame.prices)
    previous = {item.account_id: item for item in state.risks}
    risks = []
    events = list(state.events)
    for stored in state.ledger.accounts:
        account = stored.book.account
        current = evaluate(
            account, value_account(stored.book, prices), policy, previous[account.account_id]
        )
        events.extend(
            transitions(
                previous[account.account_id], current, state.frame.sequence, len(events) + 1
            )
        )
        risks.append(current)
    return replace(state, risks=tuple(risks), events=tuple(events))


def advance(
    state: TradingState,
    command: PriceFrame | OrderCommand,
    definition: RunDefinition,
    policy: RiskPolicy,
    settlement_plan: SettlementPlan | None = None,
) -> Transition:
    """相同输入得到相同成交、拒绝原因及风险事件，供在线执行与启动核对共用。"""
    if isinstance(command, PriceFrame):
        if command.sequence <= len(state.frames):
            if state.frames[command.sequence - 1] != command:
                raise CommandConflict("同一行情序号内容冲突")
            return Transition(state, "", "", FrameResult(command.sequence, True))
        if command.trading_day != state.trading_day or state.phase == "SETTLED":
            raise AccountingError("行情交易日与当前未结算交易日不一致")
        if settlement_plan is not None:
            session = settlement_plan.day(state.trading_day).session
            if command.sequence > session.close_sequence:
                raise AccountingError("行情超过当日收盘帧，需先清算并结转")
        instruments = {item.instrument_id: item for item in definition.instruments}
        prices = dict(command.prices)
        if prices.keys() != instruments.keys():
            raise AccountingError("模拟帧必须包含全部配置合约且不能包含未知合约")
        for key, price in prices.items():
            instruments[key].validate_price(price, execution=True)
        if command.sequence != len(state.frames) + 1:
            raise AccountingError("行情序号不连续，需先补齐下一帧")
        updated = _refresh(replace(state, frames=(*state.frames, command)), policy)
        payload = encode({"kind": "frame", **asdict(command)})
        result: OrderResult | FrameResult = FrameResult(command.sequence)
        record = None
        book = None
        expected_revision = None
    else:
        order = command.order
        for previous_order, previous_result in state.orders:
            if (previous_order.account_id, previous_order.request_id) == (
                order.account_id,
                order.request_id,
            ):
                if previous_order != order:
                    raise CommandConflict("同一订单编号内容冲突")
                return Transition(state, "", "", replace(previous_result, duplicate=True))
        current = next(
            (
                item
                for item in state.ledger.accounts
                if item.book.account.account_id == order.account_id
            ),
            None,
        )
        if current is None:
            raise AccountNotFound(order.account_id)
        instrument = next(
            (item for item in definition.instruments if item.instrument_id == order.instrument_id),
            None,
        )
        if instrument is None:
            raise AccountingError("订单引用未知合约")
        risk = next(item for item in state.risks if item.account_id == order.account_id)
        reason = ""
        record = None
        book = None
        expected_revision = None
        if order.trading_day != state.trading_day:
            reason = "TRADING_DAY_MISMATCH"
        elif state.phase != "OPEN":
            reason = "TRADING_DAY_CLOSED"
        elif not command.market_ready or state.frame is None:
            reason = "MARKET_UNAVAILABLE"
        elif order.offset is Offset.OPEN and risk.circuit_broken:
            reason = "CIRCUIT_BROKEN"
        elif order.offset is Offset.OPEN and risk.restricted_products:
            reason = "EXPOSURE_LIMIT"
        else:
            assert state.frame is not None
            prices = dict(state.frame.prices)
            fill = simulate_fill(order, current.book, prices[order.instrument_id])
            try:
                candidate = apply_fill(current.book, fill, instrument)
                value = value_account(candidate, prices)
                projected = evaluate(candidate.account, value, policy, risk)
                if order.offset is Offset.OPEN and projected.restricted_products:
                    reason = "PROJECTED_EXPOSURE_LIMIT"
                elif order.offset is Offset.OPEN and value.available < 0:
                    reason = "INSUFFICIENT_MARGIN"
                else:
                    book = candidate
                    with financial_context():
                        record = RecordedFill(
                            fill, "simulated-execution", candidate.fees - current.book.fees
                        )
                    expected_revision = current.revision
            except AccountingError:
                reason = "INVALID_POSITION"
        result = OrderResult(
            order.request_id,
            order.account_id,
            "REJECTED" if reason else "FILLED",
            reason or "EXECUTED",
            state.frame.sequence if state.frame else None,
            record.fill.fill_id if record else None,
            record.fill.price if record else None,
            record.fee if record else None,
            processed_at_ms=command.processed_at_ms,
        )
        updated = state
        if record is not None and book is not None:
            ledger = StoredLedger(
                tuple(
                    StoredAccount(book, current.revision + 1)
                    if item.book.account.account_id == order.account_id
                    else item
                    for item in state.ledger.accounts
                ),
                (*state.ledger.records, record),
            )
            updated = _refresh(replace(state, ledger=ledger), policy)
        updated = replace(updated, orders=(*updated.orders, (order, result)))
        payload = encode(
            {
                "kind": "order",
                "order": order,
                "market_ready": command.market_ready,
                **(
                    {"processed_at_ms": command.processed_at_ms}
                    if command.processed_at_ms is not None
                    else {}
                ),
            }
        )
    updated = replace(updated, revision=state.revision + 1)
    outcome = encode(
        {
            "result": result,
            "risks": updated.risks,
            "events": updated.events[len(state.events) :],
            "record": record,
        }
    )
    return Transition(updated, payload, outcome, result, record, book, expected_revision)


@dataclass(frozen=True)
class RiskView:
    account_id: str
    trading_day: date
    frame_sequence: int | None
    market_ready: bool
    warning: bool
    circuit_broken: bool
    restricted_products: tuple[str, ...]
    opening_allowed: bool
    blocking_reasons: tuple[str, ...]
    floating_pnl: Decimal | None
    equity: Decimal | None
    margin: Decimal | None
    available_funds: Decimal | None
    gross_exposure: Decimal | None


def risk_views(state: TradingState, market_ready: bool) -> tuple[RiskView, ...]:
    result = []
    risks = {item.account_id: item for item in state.risks}
    for stored in state.ledger.accounts:
        book = stored.book
        risk = risks[book.account.account_id]
        frame = state.frame if state.frame and state.frame.trading_day == book.trading_day else None
        value = value_account(book, dict(frame.prices)) if frame else None
        reasons = []
        if state.phase != "OPEN":
            reasons.append("TRADING_DAY_CLOSED")
        if not market_ready:
            reasons.append("MARKET_UNAVAILABLE")
        if risk.circuit_broken:
            reasons.append("CIRCUIT_BROKEN")
        if risk.restricted_products:
            reasons.append("EXPOSURE_LIMIT")
        if value is not None and value.available < 0:
            reasons.append("INSUFFICIENT_MARGIN")
        result.append(
            RiskView(
                book.account.account_id,
                book.trading_day,
                frame.sequence if frame else None,
                market_ready,
                risk.warning,
                risk.circuit_broken,
                risk.restricted_products,
                not reasons,
                tuple(reasons),
                value.floating_pnl if value else None,
                value.equity if value else None,
                value.margin if value else None,
                value.available if value else None,
                value.gross_exposure if value else None,
            )
        )
    return tuple(result)
