"""资金规模不同的账户使用统一比例对比；比例只用于展示，不参与风控判断。"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext

from cta_risk.application.trading import TradingState
from cta_risk.domain.numbers import financial_context
from cta_risk.domain.trading import RiskPolicy
from cta_risk.position.query import aggregate_exposure
from cta_risk.valuation.calculator import value_account


def percent(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator <= 0:
        return None
    with localcontext(Context(prec=38, rounding=ROUND_HALF_UP)):
        return str((numerator * 100 / denominator).quantize(Decimal("0.01"), ROUND_HALF_UP))


@dataclass(frozen=True)
class ExposureUsage:
    product_id: str
    gross_exposure: str
    limit: str
    usage_percent: str | None


@dataclass(frozen=True)
class AccountComparison:
    account_id: str
    loss_percent: str | None
    loss_limit: str
    warning_percent: str
    breaker_percent: str
    margin_percent: str | None
    exposures: tuple[ExposureUsage, ...]


def comparison(state: TradingState, policy: RiskPolicy) -> tuple[AccountComparison, ...]:
    result = []
    for stored in state.ledger.accounts:
        book = stored.book
        frame = state.frame if state.frame and state.frame.trading_day == book.trading_day else None
        value = value_account(book, dict(frame.prices)) if frame else None
        with financial_context():
            loss_limit = str(book.account.initial_capital * policy.loss_ratio)
        result.append(
            AccountComparison(
                book.account.account_id,
                percent(max(Decimal(0), -value.floating_pnl), book.account.initial_capital)
                if value
                else None,
                loss_limit,
                percent(policy.warning_ratio, Decimal(1)) or "0.00",
                percent(policy.loss_ratio, Decimal(1)) or "0.00",
                percent(value.margin, value.equity) if value else None,
                tuple(
                    ExposureUsage(
                        item.product_id,
                        str(item.gross_notional),
                        str(policy.limit_for(book.account.account_id, item.product_id)),
                        percent(
                            item.gross_notional,
                            policy.limit_for(book.account.account_id, item.product_id),
                        ),
                    )
                    for item in aggregate_exposure(value.positions)
                )
                if value
                else (),
            )
        )
    return tuple(result)
