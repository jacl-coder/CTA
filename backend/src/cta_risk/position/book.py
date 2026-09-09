"""POS-01/POS-04：从已成交记录生成新账本，错误不修改原状态。"""

from dataclasses import replace

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import (
    AccountBook,
    CloseAllocation,
    Fill,
    Instrument,
    Offset,
    PositionLot,
)
from cta_risk.domain.numbers import financial_context, money, total
from cta_risk.valuation.calculator import position_pnl


def apply_fill(book: AccountBook, fill: Fill, instrument: Instrument) -> AccountBook:
    if fill.account_id != book.account.account_id:
        raise AccountingError("成交账户与账本不一致")
    if fill.instrument_id != instrument.instrument_id:
        raise AccountingError("成交合约与规则不一致")
    rules = {item.instrument_id: item for item in book.instruments}
    if fill.instrument_id in rules and rules[fill.instrument_id] != instrument:
        raise AccountingError("同一账本中的合约规则不能改变")
    for previous in book.fills:
        if previous.fill_id == fill.fill_id:
            if previous == fill:
                return book
            raise AccountingError("相同成交编号的内容冲突")
    if book.settlement is not None:
        raise AccountingError("已清算的交易日不能追加成交")
    if fill.trading_day != book.trading_day:
        raise AccountingError("成交交易日与账本不一致")
    current_fills = [item for item in book.fills if item.trading_day == book.trading_day]
    if current_fills and fill.sequence <= current_fills[-1].sequence:
        raise AccountingError("成交顺序必须单调递增")
    instrument.validate_price(fill.price, execution=True)
    lots = list(book.lots)
    allocations: list[CloseAllocation] = []
    if fill.offset is Offset.OPEN:
        lots.append(
            PositionLot(
                fill.fill_id,
                fill.instrument_id,
                fill.side,
                fill.trading_day,
                fill.sequence,
                fill.quantity,
                fill.price,
                fill.price,
            )
        )
    else:
        candidates = sorted(
            (
                lot
                for lot in lots
                if lot.instrument_id == fill.instrument_id
                and lot.side is fill.side
                and (
                    lot.opened_day == book.trading_day
                    if fill.offset is Offset.CLOSE_TODAY
                    else lot.opened_day < book.trading_day
                )
            ),
            key=lambda lot: (lot.opened_day, lot.opened_sequence, lot.lot_id),
        )
        if sum(lot.quantity for lot in candidates) < fill.quantity:
            raise AccountingError("指定今昨仓类别的可平数量不足")
        remaining = fill.quantity
        for lot in candidates:
            if remaining == 0:
                break
            closed = min(remaining, lot.quantity)
            pnl = position_pnl(fill.side, fill.price, lot.basis, closed, instrument.multiplier)
            allocations.append(
                CloseAllocation(fill.fill_id, lot.lot_id, closed, lot.basis, fill.price, pnl)
            )
            index = lots.index(lot)
            if closed == lot.quantity:
                lots.pop(index)
            else:
                lots[index] = replace(lot, quantity=lot.quantity - closed)
            remaining -= closed
    if instrument.instrument_id not in rules:
        rules[instrument.instrument_id] = instrument
    with financial_context():
        return replace(
            book,
            instruments=tuple(rules.values()),
            lots=tuple(lots),
            fills=(*book.fills, fill),
            close_allocations=(*book.close_allocations, *allocations),
            realized_pnl=book.realized_pnl + total(item.pnl for item in allocations),
            fees=book.fees + money(instrument.fee_per_lot * fill.quantity),
        )
