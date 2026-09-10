"""同步全量模拟执行：成交价来自引擎有效行情，用户不能指定成交价。"""

from decimal import Decimal

from cta_risk.domain.models import AccountBook, Fill
from cta_risk.domain.trading import Order


def simulate_fill(order: Order, book: AccountBook, price: Decimal) -> Fill:
    sequence = (
        max(
            (fill.sequence for fill in book.fills if fill.trading_day == book.trading_day),
            default=0,
        )
        + 1
    )
    fill_id = f"order:{order.request_id}"
    if any(fill.fill_id == fill_id for fill in book.fills):
        # Bootstrap IDs can use arbitrary strings; never turn a new order into a duplicate fill.
        fill_id = f"order:{order.request_id}:{sequence}"
        while any(fill.fill_id == fill_id for fill in book.fills):
            fill_id += ":new"
    return Fill(
        fill_id,
        order.account_id,
        order.instrument_id,
        order.trading_day,
        sequence,
        order.side,
        order.offset,
        order.quantity,
        price,
    )
