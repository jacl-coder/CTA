"""本地模拟交易适配：所有写入都进入同一应用队列。"""

from dataclasses import asdict
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from cta_risk.api.ledger import Service
from cta_risk.api.security import check_origin, require_session
from cta_risk.application.trading import OrderResult, risk_views
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.trading import Order, PriceFrame

router = APIRouter(prefix="/api", tags=["trading"])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OrderInput(StrictInput):
    request_id: str
    account_id: str
    instrument_id: str
    trading_day: str
    side: Literal["LONG", "SHORT"]
    offset: Literal["OPEN", "CLOSE_TODAY", "CLOSE_YESTERDAY"]
    quantity: int

    def to_domain(self) -> Order:
        return Order(
            self.request_id,
            self.account_id,
            self.instrument_id,
            parse_day(self.trading_day),
            Side(self.side),
            Offset(self.offset),
            self.quantity,
        )


def parse_day(value: str) -> date:
    try:
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError
        return day
    except ValueError as exc:
        raise AccountingError("交易日必须使用 YYYY-MM-DD") from exc


class FrameInput(StrictInput):
    sequence: int
    trading_day: str
    prices: dict[str, str]

    def to_domain(self) -> PriceFrame:
        return PriceFrame(
            self.sequence,
            parse_day(self.trading_day),
            tuple(sorted((key, decimal_value(value)) for key, value in self.prices.items())),
        )


class OrderResponse(BaseModel):
    request_id: str
    account_id: str
    status: Literal["FILLED", "REJECTED"]
    reason: str
    frame_sequence: int | None
    fill_id: str | None
    price: str | None
    fee: str | None
    duplicate: bool
    processed_at_ms: int | None = None

    @classmethod
    def from_result(cls, result: OrderResult) -> "OrderResponse":
        return cls(
            **{
                **asdict(result),
                "price": str(result.price) if result.price is not None else None,
                "fee": str(result.fee) if result.fee is not None else None,
            }
        )


class FrameResponse(BaseModel):
    sequence: int
    duplicate: bool


class SessionResponse(BaseModel):
    token: str


class MarketResponse(BaseModel):
    sequence: int | None
    trading_day: date | None
    source: str | None
    prices: dict[str, str]
    market_ready: bool


class RiskResponse(BaseModel):
    account_id: str
    trading_day: date
    frame_sequence: int | None
    market_ready: bool
    warning: bool
    circuit_broken: bool
    restricted_products: tuple[str, ...]
    opening_allowed: bool
    blocking_reasons: tuple[str, ...]
    floating_pnl: str | None
    equity: str | None
    margin: str | None
    available_funds: str | None
    gross_exposure: str | None


class RiskEventResponse(BaseModel):
    event_id: int
    account_id: str
    frame_sequence: int
    kind: str
    product_id: str | None
    occurred_ms: int | None = None
    detected_ms: int | None = None
    recovered: bool | None = None
    trading_day: date | None = None


@router.get("/session", operation_id="getLocalSession", response_model=SessionResponse)
async def session(request: Request, service: Service) -> JSONResponse:
    check_origin(request)
    service.trading_state()
    return JSONResponse(
        {"token": request.app.state.write_token}, headers={"Cache-Control": "no-store"}
    )


@router.post("/orders", operation_id="submitOrder", dependencies=[Depends(require_session)])
async def submit_order(body: OrderInput, service: Service) -> OrderResponse:
    return OrderResponse.from_result(await service.submit_order(body.to_domain()))


@router.post(
    "/simulation/frames",
    operation_id="publishSimulationFrame",
    dependencies=[Depends(require_session)],
)
async def publish_frame(body: FrameInput, service: Service) -> FrameResponse:
    result = await service.publish_frame(body.to_domain())
    return FrameResponse(sequence=result.sequence, duplicate=result.duplicate)


@router.get("/market", operation_id="getMarketSnapshot")
async def market(service: Service) -> MarketResponse:
    frame = service.trading_state().frame
    return MarketResponse(
        sequence=frame.sequence if frame else None,
        trading_day=frame.trading_day if frame else None,
        source=frame.source if frame else None,
        prices={key: str(value) for key, value in frame.prices} if frame else {},
        market_ready=service.trading_available,
    )


@router.get("/risk", operation_id="listAccountRisk")
async def risk(service: Service, account_id: str | None = None) -> list[RiskResponse]:
    if account_id is not None:
        service.account(account_id)
    result = []
    for view in risk_views(service.trading_state(), service.trading_available):
        if account_id is not None and view.account_id != account_id:
            continue
        raw = asdict(view)
        for key in ("floating_pnl", "equity", "margin", "available_funds", "gross_exposure"):
            raw[key] = str(raw[key]) if raw[key] is not None else None
        result.append(RiskResponse(**raw))
    return result


@router.get("/risk-events", operation_id="listRiskEvents")
async def risk_events(
    service: Service,
    account_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[RiskEventResponse]:
    if account_id is not None:
        service.account(account_id)
    events = [
        item
        for item in service.trading_state().events
        if account_id is None or item.account_id == account_id
    ]
    observations = (
        {item.event_id: item for item in service.source_state().observations}
        if service.market_plan
        else {}
    )
    return [
        RiskEventResponse(
            **{
                **asdict(item),
                "trading_day": service.trading_state().frames[item.frame_sequence - 1].trading_day,
                **(asdict(observations[item.event_id]) if item.event_id in observations else {}),
            }
        )
        for item in events[offset : offset + limit]
    ]


@router.get("/orders", operation_id="listOrders")
async def orders(
    service: Service,
    account_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[OrderResponse]:
    if account_id is not None:
        service.account(account_id)
    results = [
        item
        for _, item in service.trading_state().orders
        if account_id is None or item.account_id == account_id
    ]
    return [OrderResponse.from_result(item) for item in results[offset : offset + limit]]


@router.get("/orders/{account_id}/{request_id}", operation_id="getOrder")
async def order(service: Service, account_id: str, request_id: str) -> OrderResponse:
    service.account(account_id)
    for _, result in service.trading_state().orders:
        if (result.account_id, result.request_id) == (account_id, request_id):
            return OrderResponse.from_result(result)
    raise HTTPException(404, "订单不存在")
