"""显式精度、输入约束与统一入账舍入，不修改全局 Decimal 上下文。"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    localcontext,
)

from cta_risk.domain.errors import AccountingError

ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def decimal_value(value: object, name: str = "数值") -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise AccountingError(f"{name}必须是 Decimal 或十进制字符串")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise AccountingError(f"{name}不是有效十进制数") from exc
    # Bounded inputs make the chosen arithmetic precision explainable.
    if not result.is_finite():
        raise AccountingError(f"{name}必须是有限数")
    exponent = result.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -8 or result.adjusted() > 18:
        raise AccountingError(f"{name}超出范围（最多 19 位整数、8 位小数）")
    return result


def positive_quantity(value: int, name: str = "数量") -> None:
    if type(value) is not int or not 0 < value <= 1_000_000:
        raise AccountingError(f"{name}必须是 1 至 1000000 的整数")


@contextmanager
def financial_context() -> Iterator[None]:
    context = Context(prec=38, rounding=ROUND_HALF_UP)
    context.traps[Inexact] = True
    try:
        with localcontext(context):
            yield
    except DecimalException as exc:
        raise AccountingError("计算超出金融精度范围，拒绝静默舍入") from exc


def money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise AccountingError("金额必须是有限 Decimal")
    with localcontext(Context(prec=38, rounding=ROUND_HALF_UP)):
        return value.quantize(CENT)


def total(values: Iterator[Decimal]) -> Decimal:
    with financial_context():
        return sum(values, ZERO)
