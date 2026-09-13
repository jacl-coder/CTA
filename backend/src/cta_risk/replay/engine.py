"""历史输入按原顺序执行两次，用同一交易/清算规则对照不同风控参数。"""

import hashlib
from dataclasses import asdict

from pydantic import BaseModel

from cta_risk.application.analytics import comparison
from cta_risk.application.ledger import seed_ledger
from cta_risk.application.settlement import advance_day
from cta_risk.application.trading import (
    OrderResult,
    TradingState,
    advance,
    encode,
    initial_state,
    risk_views,
)
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.settlement import DayCommand
from cta_risk.domain.trading import PriceFrame
from cta_risk.replay.models import ReplayDataset, ReplayRequest


class ReplayAccount(BaseModel):
    account_id: str
    equity: str | None
    floating_pnl: str | None
    margin: str | None
    available_funds: str | None
    loss_percent: str | None
    settled_balance: str | None
    circuit_broken: bool
    warning: bool
    restricted_products: list[str]
    opening_allowed: bool


class ReplayEvent(BaseModel):
    step: int
    trading_day: str
    account_id: str
    frame_sequence: int
    kind: str
    product_id: str | None


class ReplayOrderResult(BaseModel):
    account_id: str
    request_id: str
    status: str
    reason: str
    price: str | None
    fee: str | None
    duplicate: bool


class ReplayPoint(BaseModel):
    step: int
    kind: str
    trading_day: str
    frame_sequence: int | None
    phase: str
    prices: dict[str, str]
    accounts: list[ReplayAccount]
    order: ReplayOrderResult | None


class ReplayResult(BaseModel):
    policy: TradingSettings
    points: list[ReplayPoint]
    events: list[ReplayEvent]
    report_count: int
    state_digest: str
    matches_recording: bool | None


class ReplayComparison(BaseModel):
    name: str
    dataset_digest: str
    baseline: ReplayResult
    candidate: ReplayResult


def state_digest(state: TradingState) -> str:
    return hashlib.sha256(encode(state).encode()).hexdigest()


def run(dataset: ReplayDataset, settings: TradingSettings) -> ReplayResult:
    ledger = dataset.seed.ledger()
    definition = ledger.definition()
    policy = settings.policy()
    account_ids = {item.account_id for item in definition.accounts}
    products = {item.product_id for item in definition.instruments}
    if any(
        item.account_id not in account_ids or item.product_id not in products
        for item in policy.product_limits
    ):
        raise AccountingError("敞口限额引用未知账户或品种")
    if len(dataset.steps) * len(account_ids) > 30000:
        raise AccountingError("回放最多支持 30000 个步骤×账户，请缩小历史数据规模")
    plan = dataset.plan(ledger)
    state = initial_state(seed_ledger(definition))
    points, events = [], []
    for index, step in enumerate(dataset.steps, 1):
        try:
            command = step.command()
            old_events = len(state.events)
            if isinstance(command, DayCommand):
                if plan is None:
                    raise AccountingError("日结命令缺少结算计划")
                transition = advance_day(state, command, definition, policy, plan)
                state = transition.state
                result = None
            else:
                trade = advance(state, command, definition, policy, plan)
                state = trade.state
                result = trade.result if isinstance(trade.result, OrderResult) else None
            frame = state.frame
            ready = bool(frame and frame.trading_day == state.trading_day and state.phase == "OPEN")
            if not isinstance(command, (PriceFrame, DayCommand)):
                ready = ready and command.market_ready
            metrics = {item.account_id: item for item in comparison(state, policy)}
            accounts = []
            for view in risk_views(state, ready):
                accounts.append(
                    ReplayAccount(
                        account_id=view.account_id,
                        **{
                            key: str(getattr(view, key)) if getattr(view, key) is not None else None
                            for key in ("equity", "floating_pnl", "margin", "available_funds")
                        },
                        loss_percent=metrics[view.account_id].loss_percent,
                        settled_balance=next(
                            (
                                str(item.book.settlement.closing_balance)
                                for item in state.ledger.accounts
                                if item.book.account.account_id == view.account_id
                                and item.book.settlement is not None
                            ),
                            None,
                        ),
                        circuit_broken=view.circuit_broken,
                        warning=view.warning,
                        restricted_products=list(view.restricted_products),
                        opening_allowed=view.opening_allowed,
                    )
                )
            points.append(
                ReplayPoint(
                    step=index,
                    kind=step.kind,
                    trading_day=str(state.trading_day),
                    frame_sequence=frame.sequence if frame else None,
                    phase=state.phase,
                    prices={key: str(value) for key, value in frame.prices} if frame else {},
                    accounts=accounts,
                    order=ReplayOrderResult(
                        account_id=result.account_id,
                        request_id=result.request_id,
                        status=result.status,
                        reason=result.reason,
                        price=str(result.price) if result.price is not None else None,
                        fee=str(result.fee) if result.fee is not None else None,
                        duplicate=result.duplicate,
                    )
                    if result
                    else None,
                )
            )
            for event in state.events[old_events:]:
                raw = asdict(event)
                raw.pop("event_id")
                events.append(ReplayEvent(step=index, trading_day=str(state.trading_day), **raw))
        except (ValueError, LookupError, ArithmeticError) as exc:
            raise AccountingError(f"第 {index} 步（{step.kind}）回放失败：{exc}") from exc
    digest = state_digest(state)
    return ReplayResult(
        policy=settings,
        points=points,
        events=events,
        report_count=len(state.reports),
        state_digest=digest,
        matches_recording=digest == dataset.expected_digest if dataset.expected_digest else None,
    )


def compare(request: ReplayRequest) -> ReplayComparison:
    dataset = request.dataset
    baseline = run(dataset, dataset.policy)
    candidate = run(dataset, request.candidate_policy) if request.candidate_policy else baseline
    return ReplayComparison(
        name=dataset.name,
        dataset_digest=hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
        baseline=baseline,
        candidate=candidate,
    )
