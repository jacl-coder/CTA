"""资金与持仓规则：当日浮亏熔断锁存，敞口限制与告警分别转换。"""

from dataclasses import dataclass

from cta_risk.domain.models import Account, Valuation
from cta_risk.domain.numbers import ZERO, financial_context
from cta_risk.domain.trading import RiskPolicy
from cta_risk.position.query import aggregate_exposure


@dataclass(frozen=True)
class RiskState:
    account_id: str
    warning: bool = False
    circuit_broken: bool = False
    restricted_products: tuple[str, ...] = ()


@dataclass(frozen=True)
class RiskEvent:
    event_id: int
    account_id: str
    frame_sequence: int
    kind: str
    product_id: str | None = None


def evaluate(
    account: Account, value: Valuation, policy: RiskPolicy, previous: RiskState
) -> RiskState:
    with financial_context():
        loss = max(ZERO, -value.floating_pnl)
        return RiskState(
            account.account_id,
            loss >= account.initial_capital * policy.warning_ratio,
            previous.circuit_broken or loss >= account.initial_capital * policy.loss_ratio,
            tuple(
                item.product_id
                for item in aggregate_exposure(value.positions)
                if item.gross_notional > policy.limit_for(account.account_id, item.product_id)
            ),
        )


def transitions(
    previous: RiskState, current: RiskState, frame: int, start: int
) -> tuple[RiskEvent, ...]:
    changes: list[tuple[str, str | None]] = []
    if current.warning != previous.warning:
        changes.append(("LOSS_WARNING_ENTER" if current.warning else "LOSS_WARNING_EXIT", None))
    if current.circuit_broken and not previous.circuit_broken:
        changes.append(("CIRCUIT_BREAK", None))
    for product in sorted(set(current.restricted_products) - set(previous.restricted_products)):
        changes.append(("EXPOSURE_LIMIT_ENTER", product))
    for product in sorted(set(previous.restricted_products) - set(current.restricted_products)):
        changes.append(("EXPOSURE_LIMIT_EXIT", product))
    return tuple(
        RiskEvent(start + i, current.account_id, frame, kind, product)
        for i, (kind, product) in enumerate(changes)
    )
