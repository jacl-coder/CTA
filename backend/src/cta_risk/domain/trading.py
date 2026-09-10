"""交易命令、完整模拟价格帧与风险配置；均为不可变领域值。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import Offset, Side, identifier
from cta_risk.domain.numbers import decimal_value, positive_quantity


class CommandConflict(AccountingError):
    """同一业务编号不可用于不同内容。"""


@dataclass(frozen=True)
class Order:
    request_id: str
    account_id: str
    instrument_id: str
    trading_day: date
    side: Side
    offset: Offset
    quantity: int

    def __post_init__(self) -> None:
        for value in (self.request_id, self.account_id, self.instrument_id):
            identifier(value)
        positive_quantity(self.quantity)
        if type(self.trading_day) is not date or not isinstance(self.side, Side):
            raise AccountingError("订单交易日或方向非法")
        if not isinstance(self.offset, Offset):
            raise AccountingError("订单开平意图非法")


@dataclass(frozen=True)
class PriceFrame:
    sequence: int
    trading_day: date
    prices: tuple[tuple[str, Decimal], ...]
    source: str = "manual-demo"

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 0 < self.sequence < 2**63:
            raise AccountingError("行情序号必须为正整数")
        if type(self.trading_day) is not date:
            raise AccountingError("行情交易日非法")
        identifier(self.source)
        if type(self.prices) is not tuple or not self.prices:
            raise AccountingError("行情必须包含不可变的价格集合")
        if len(dict(self.prices)) != len(self.prices):
            raise AccountingError("行情包含重复合约")
        if self.prices != tuple(sorted(self.prices)):
            raise AccountingError("行情合约必须排序")
        for instrument, price in self.prices:
            identifier(instrument)
            if not isinstance(price, Decimal) or decimal_value(price) <= 0:
                raise AccountingError("行情价格必须为正 Decimal")


@dataclass(frozen=True)
class ProductLimit:
    account_id: str
    product_id: str
    amount: Decimal

    def __post_init__(self) -> None:
        identifier(self.account_id)
        identifier(self.product_id)
        if not isinstance(self.amount, Decimal) or decimal_value(self.amount) <= 0:
            raise AccountingError("敞口限额必须为正 Decimal")


@dataclass(frozen=True)
class RiskPolicy:
    warning_ratio: Decimal
    loss_ratio: Decimal
    default_product_limit: Decimal
    product_limits: tuple[ProductLimit, ...] = ()
    max_price_age_seconds: int = 30

    def __post_init__(self) -> None:
        for value in (self.warning_ratio, self.loss_ratio, self.default_product_limit):
            if not isinstance(value, Decimal):
                raise AccountingError("风险参数必须为 Decimal")
            decimal_value(value)
        if not 0 < self.warning_ratio < self.loss_ratio <= 1:
            raise AccountingError("必须满足 0 < 告警比例 < 熔断比例 <= 1")
        if self.default_product_limit <= 0:
            raise AccountingError("默认敞口限额必须为正")
        if (
            type(self.max_price_age_seconds) is not int
            or not 1 <= self.max_price_age_seconds <= 3600
        ):
            raise AccountingError("价格有效期必须为 1 至 3600 秒")
        if type(self.product_limits) is not tuple or len(
            {(item.account_id, item.product_id) for item in self.product_limits}
        ) != len(self.product_limits):
            raise AccountingError("品种限额必须为不可变且不重复的集合")

    def limit_for(self, account_id: str, product_id: str) -> Decimal:
        return next(
            (
                item.amount
                for item in self.product_limits
                if (item.account_id, item.product_id) == (account_id, product_id)
            ),
            self.default_product_limit,
        )
