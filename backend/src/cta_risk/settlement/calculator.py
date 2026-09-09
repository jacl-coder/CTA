"""SET-01：日结计算与跨日结转。持久化原子提交由应用层负责。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, DaySettlement, SettlementLine
from cta_risk.domain.numbers import ZERO, financial_context
from cta_risk.valuation.calculator import position_pnl, value_account


def settle_account(book: AccountBook, prices: Mapping[str, Decimal]) -> AccountBook:
    required = sorted({lot.instrument_id for lot in book.lots})
    for instrument_id in required:
        if instrument_id not in prices:
            raise AccountingError(f"缺少结算价：{instrument_id}")
        book.instrument(instrument_id).validate_price(prices[instrument_id])
    effective_prices = tuple((key, prices[key]) for key in required)
    if book.settlement is not None:
        if book.settlement.prices != effective_prices:
            raise AccountingError("结算价改变，拒绝覆盖已有日结")
        return book
    valuation = value_account(book, prices)
    with financial_context():
        lines = tuple(
            SettlementLine(
                lot.lot_id,
                lot.instrument_id,
                lot.side,
                lot.quantity,
                lot.basis,
                prices[lot.instrument_id],
                position_pnl(
                    lot.side,
                    prices[lot.instrument_id],
                    lot.basis,
                    lot.quantity,
                    book.instrument(lot.instrument_id).multiplier,
                ),
            )
            for lot in book.lots
        )
        result = DaySettlement(
            book.account.account_id,
            book.trading_day,
            effective_prices,
            lines,
            book.opening_balance,
            book.realized_pnl,
            valuation.floating_pnl,
            book.fees,
            book.realized_pnl + valuation.floating_pnl - book.fees,
            valuation.equity,
            valuation.margin,
            valuation.available,
        )
    return replace(book, settlement=result)


def start_next_day(book: AccountBook, trading_day: date) -> AccountBook:
    if book.settlement is None:
        raise AccountingError("前一交易日尚未清算")
    if type(trading_day) is not date or trading_day <= book.trading_day:
        raise AccountingError("下一交易日必须晚于当前交易日")
    prices = dict(book.settlement.prices)
    return replace(
        book,
        trading_day=trading_day,
        opening_balance=book.settlement.closing_balance,
        lots=tuple(replace(lot, basis=prices[lot.instrument_id]) for lot in book.lots),
        close_allocations=(),
        realized_pnl=ZERO,
        fees=ZERO,
        settlement=None,
    )
