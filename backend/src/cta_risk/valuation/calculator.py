"""SET-01/RISK-02 的计算基础；价格新鲜度由未来行情应用层校验。"""

from collections.abc import Mapping
from decimal import Decimal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, PositionValue, Side, Valuation
from cta_risk.domain.numbers import ZERO, financial_context, money, total


def position_pnl(
    side: Side, price: Decimal, basis: Decimal, quantity: int, multiplier: int
) -> Decimal:
    """平仓、盘中估值和结算共享的明细盈亏公式。"""
    with financial_context():
        return money(side.sign * (price - basis) * quantity * multiplier)


def value_account(book: AccountBook, prices: Mapping[str, Decimal]) -> Valuation:
    grouped: dict[tuple[str, Side], tuple[int, Decimal]] = {}
    with financial_context():
        for lot in book.lots:
            instrument = book.instrument(lot.instrument_id)
            if lot.instrument_id not in prices:
                raise AccountingError(f"缺少持仓合约价格：{lot.instrument_id}")
            price = prices[lot.instrument_id]
            instrument.validate_price(price)
            pnl = position_pnl(lot.side, price, lot.basis, lot.quantity, instrument.multiplier)
            key = (lot.instrument_id, lot.side)
            quantity, accumulated = grouped.get(key, (0, ZERO))
            grouped[key] = (quantity + lot.quantity, accumulated + pnl)
        positions = []
        for (instrument_id, side), (quantity, pnl) in sorted(grouped.items()):
            instrument = book.instrument(instrument_id)
            notional = prices[instrument_id] * quantity * instrument.multiplier
            positions.append(
                PositionValue(
                    book.account.account_id,
                    instrument_id,
                    instrument.product_id,
                    side,
                    quantity,
                    notional,
                    money(notional * instrument.margin_rate),
                    pnl,
                )
            )
        floating = total(item.floating_pnl for item in positions)
        margin = total(item.margin for item in positions)
        equity = book.opening_balance + book.realized_pnl + floating - book.fees
        return Valuation(
            tuple(positions),
            floating,
            equity,
            margin,
            equity - margin,
            total(item.notional for item in positions),
            total(item.notional * item.side.sign for item in positions),
        )
