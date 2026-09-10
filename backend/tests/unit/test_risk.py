from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from pydantic import ValidationError

from cta_risk.config.models import Settings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.models import Account, Valuation
from cta_risk.domain.trading import RiskPolicy
from cta_risk.risk.rules import RiskState, evaluate, transitions

D = Decimal


@pytest.mark.requirement("RISK-02", "RISK-04")
@pytest.mark.parametrize(
    "floating,warning,broken",
    [
        ("-1999.99", False, False),
        ("-2000", True, False),
        ("-2999.99", True, False),
        ("-3000", True, True),
        ("-3000.01", True, True),
    ],
)
def test_risk_uses_floating_loss_not_total_equity(
    floating: str, warning: bool, broken: bool
) -> None:
    account = Account("A", D("100000"))
    policy = RiskPolicy(D(".02"), D(".03"), D("2000000"))
    # Equity intentionally includes unrelated realized P&L; only floating loss sets the breaker.
    value = Valuation((), D(floating), D("1"), D("0"), D("1"), D("0"), D("0"))
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        state = evaluate(account, value, policy, RiskState("A"))
    assert (state.warning, state.circuit_broken) == (warning, broken)
    assert evaluate(
        account, replace(value, floating_pnl=D("0")), policy, replace(state, circuit_broken=True)
    ).circuit_broken


def test_warning_can_reenter_but_breaker_only_enters_once() -> None:
    normal = RiskState("A")
    alert = RiskState("A", warning=True, circuit_broken=True)
    assert [event.kind for event in transitions(normal, alert, 1, 1)] == [
        "LOSS_WARNING_ENTER",
        "CIRCUIT_BREAK",
    ]
    cleared = replace(alert, warning=False)
    assert [event.kind for event in transitions(alert, cleared, 2, 3)] == ["LOSS_WARNING_EXIT"]
    assert [event.kind for event in transitions(cleared, alert, 3, 4)] == ["LOSS_WARNING_ENTER"]


@pytest.mark.parametrize(
    "settings",
    [
        {"warning_ratio": "0.03", "loss_ratio": "0.02"},
        {"loss_ratio": "NaN"},
        {"default_product_limit": "0"},
        {"loss_ratio": 0.03},
        {"max_price_age_seconds": 0},
        {"product_limits": [{"account_id": "A", "product_id": "SHFE.rb", "amount": "1"}] * 2},
    ],
)
def test_invalid_risk_configuration(settings: dict) -> None:
    with pytest.raises(ValidationError):
        TradingSettings.model_validate(settings)


def test_risk_config_requires_ledger_and_known_references(demo_settings: Settings) -> None:
    with pytest.raises(ValidationError):
        Settings(trading=TradingSettings())
    with pytest.raises(ValidationError):
        Settings(
            ledger=demo_settings.ledger,
            trading=TradingSettings.model_validate(
                {
                    "product_limits": [
                        {"account_id": "missing", "product_id": "SHFE.rb", "amount": "1"}
                    ]
                }
            ),
        )
