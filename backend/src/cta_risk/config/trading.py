"""首版资金/持仓规则配置，转换为纯领域策略。"""

from typing import Self

from pydantic import field_validator, model_validator

from cta_risk.config.ledger import LedgerModel
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.trading import ProductLimit, RiskPolicy


class ProductLimitConfig(LedgerModel):
    account_id: str
    product_id: str
    amount: str


class TradingSettings(LedgerModel):
    warning_ratio: str = "0.02"
    loss_ratio: str = "0.03"
    default_product_limit: str = "1000000"
    product_limits: tuple[ProductLimitConfig, ...] = ()
    max_price_age_seconds: int = 30

    @field_validator("product_limits", mode="before")
    @classmethod
    def as_tuple(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    def policy(self) -> RiskPolicy:
        return RiskPolicy(
            decimal_value(self.warning_ratio),
            decimal_value(self.loss_ratio),
            decimal_value(self.default_product_limit),
            tuple(
                ProductLimit(item.account_id, item.product_id, decimal_value(item.amount))
                for item in self.product_limits
            ),
            self.max_price_age_seconds,
        )

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        self.policy()
        return self
