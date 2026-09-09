"""POS 查询 HTTP 适配，仅依赖应用契约，不提供原始成交或下单写接口。"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from cta_risk.application.contracts import AccountNotFound, StoredAccount
from cta_risk.application.ledger import LedgerService
from cta_risk.domain.models import Offset, Side

router = APIRouter(prefix="/api", tags=["ledger"])


def ledger_service(request: Request) -> LedgerService:
    service = request.app.state.ledger
    if not isinstance(service, LedgerService) or not service.available:
        raise HTTPException(503, "账本服务未就绪或存储不可用")
    return service


Service = Annotated[LedgerService, Depends(ledger_service)]


class AccountResponse(BaseModel):
    account_id: str
    trading_day: date
    initial_capital: str
    opening_balance: str
    realized_pnl: str
    fees: str
    revision: int

    @classmethod
    def from_stored(cls, stored: StoredAccount) -> "AccountResponse":
        book = stored.book
        return cls(
            account_id=book.account.account_id,
            trading_day=book.trading_day,
            initial_capital=str(book.account.initial_capital),
            opening_balance=str(book.opening_balance),
            realized_pnl=str(book.realized_pnl),
            fees=str(book.fees),
            revision=stored.revision,
        )


class PositionResponse(BaseModel):
    account_id: str
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    today_quantity: int
    yesterday_quantity: int


class PositionAggregateResponse(BaseModel):
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    today_quantity: int
    yesterday_quantity: int


class TradeResponse(BaseModel):
    fill_id: str
    account_id: str
    instrument_id: str
    trading_day: date
    sequence: int
    side: Side
    offset: Offset
    quantity: int
    price: str
    fee: str
    source: str


@router.get("/accounts", operation_id="listAccounts")
async def accounts(service: Service) -> list[AccountResponse]:
    return [AccountResponse.from_stored(item) for item in service.accounts()]


@router.get("/accounts/{account_id}", operation_id="getAccount")
async def account(account_id: str, service: Service) -> AccountResponse:
    try:
        return AccountResponse.from_stored(service.account(account_id))
    except AccountNotFound as exc:
        raise HTTPException(404, "账户不存在") from exc


@router.get("/positions", operation_id="listPositions")
async def positions(
    service: Service,
    account_id: str | None = None,
    product_id: str | None = None,
) -> list[PositionResponse]:
    try:
        return [
            PositionResponse(
                account_id=item.account_id,
                instrument_id=item.instrument_id,
                product_id=item.product_id,
                side=item.side,
                quantity=item.quantity,
                today_quantity=item.today_quantity,
                yesterday_quantity=item.yesterday_quantity,
            )
            for item in service.positions(account_id, product_id)
        ]
    except AccountNotFound as exc:
        raise HTTPException(404, "账户不存在") from exc


@router.get("/positions/summary", operation_id="summarizePositions")
async def position_summary(
    service: Service, product_id: str | None = None
) -> list[PositionAggregateResponse]:
    return [
        PositionAggregateResponse(
            instrument_id=item.instrument_id,
            product_id=item.product_id,
            side=item.side,
            quantity=item.quantity,
            today_quantity=item.today_quantity,
            yesterday_quantity=item.yesterday_quantity,
        )
        for item in service.position_summary(product_id)
    ]


@router.get("/trades", operation_id="listTrades")
async def trades(
    service: Service,
    account_id: str | None = None,
    product_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[TradeResponse]:
    try:
        records = service.trades(account_id, product_id)
    except AccountNotFound as exc:
        raise HTTPException(404, "账户不存在") from exc
    return [
        TradeResponse(
            fill_id=record.fill.fill_id,
            account_id=record.fill.account_id,
            instrument_id=record.fill.instrument_id,
            trading_day=record.fill.trading_day,
            sequence=record.fill.sequence,
            side=record.fill.side,
            offset=record.fill.offset,
            quantity=record.fill.quantity,
            price=str(record.fill.price),
            fee=str(record.fee),
            source=record.source,
        )
        for record in records[offset : offset + limit]
    ]
