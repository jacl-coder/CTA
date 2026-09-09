"""POS-02/POS-04：账户/品种查询及明确区分净额和绝对额的汇总。"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import AccountBook, PositionSummary, PositionValue, Side
from cta_risk.domain.numbers import ZERO, financial_context


@dataclass(frozen=True)
class ProductExposure:
    product_id: str
    long_notional: Decimal
    short_notional: Decimal
    gross_notional: Decimal
    net_notional: Decimal


@dataclass(frozen=True)
class PositionAggregate:
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    today_quantity: int
    yesterday_quantity: int


def aggregate_positions(positions: Iterable[PositionSummary]) -> tuple[PositionAggregate, ...]:
    """仅合并同合约同方向的手数；不跨合约相加或抵扣多空。"""
    grouped: dict[tuple[str, str, Side], tuple[int, int]] = {}
    for item in positions:
        key = item.instrument_id, item.product_id, item.side
        today, yesterday = grouped.get(key, (0, 0))
        grouped[key] = today + item.today_quantity, yesterday + item.yesterday_quantity
    return tuple(
        PositionAggregate(instrument, product, side, today + yesterday, today, yesterday)
        for (instrument, product, side), (today, yesterday) in sorted(grouped.items())
    )


def query_positions(
    books: Iterable[AccountBook],
    *,
    account_id: str | None = None,
    product_id: str | None = None,
) -> tuple[PositionSummary, ...]:
    result: list[PositionSummary] = []
    seen: set[str] = set()
    for book in books:
        identifier = book.account.account_id
        if identifier in seen:
            raise AccountingError("查询输入不能包含同一账户的多个账本")
        seen.add(identifier)
        if account_id is not None and identifier != account_id:
            continue
        grouped: dict[tuple[str, Side], tuple[int, int]] = {}
        for lot in book.lots:
            instrument = book.instrument(lot.instrument_id)
            if product_id is not None and instrument.product_id != product_id:
                continue
            today, yesterday = grouped.get((lot.instrument_id, lot.side), (0, 0))
            if lot.opened_day == book.trading_day:
                today += lot.quantity
            else:
                yesterday += lot.quantity
            grouped[(lot.instrument_id, lot.side)] = (today, yesterday)
        for (instrument_id, side), (today, yesterday) in grouped.items():
            result.append(
                PositionSummary(
                    identifier,
                    instrument_id,
                    book.instrument(instrument_id).product_id,
                    side,
                    today + yesterday,
                    today,
                    yesterday,
                )
            )
    return tuple(sorted(result, key=lambda item: (item.account_id, item.instrument_id, item.side)))


def aggregate_exposure(positions: Iterable[PositionValue]) -> tuple[ProductExposure, ...]:
    grouped: dict[str, tuple[Decimal, Decimal]] = {}
    with financial_context():
        for position in positions:
            long, short = grouped.get(position.product_id, (ZERO, ZERO))
            if position.side is Side.LONG:
                long += position.notional
            else:
                short += position.notional
            grouped[position.product_id] = long, short
        return tuple(
            ProductExposure(product, long, short, long + short, long - short)
            for product, (long, short) in sorted(grouped.items())
        )
