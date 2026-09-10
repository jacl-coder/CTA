"""前端工作区只读快照；读取期间不向事件循环让出执行权。"""

import time
from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from cta_risk.api.ledger import AccountResponse, PositionResponse, accounts, positions
from cta_risk.api.market import SourcesResponse, sources
from cta_risk.api.routes import system_status
from cta_risk.api.schemas import SystemResponse
from cta_risk.api.settlement import SessionStatus, status
from cta_risk.api.trading import MarketResponse, RiskResponse, market, risk
from cta_risk.application.contracts import LedgerUnavailable
from cta_risk.application.ledger import LedgerService
from cta_risk.domain.numbers import total
from cta_risk.settlement.scheduler import SettlementScheduler

router = APIRouter(prefix="/api", tags=["workspace"])


class InstrumentResponse(BaseModel):
    instrument_id: str
    product_id: str
    multiplier: int
    tick_size: str
    margin_rate: str
    fee_per_lot: str


class SettlementDayResponse(BaseModel):
    trading_day: str
    close_sequence: int
    prices: dict[str, str]


class Capabilities(BaseModel):
    ledger: bool
    trading: bool
    manual_market: bool
    multi_source: bool
    settlement: bool


class Totals(BaseModel):
    equity: str | None
    floating_pnl: str | None
    margin: str | None
    available_funds: str | None
    gross_exposure: str | None

    @classmethod
    def from_risks(cls, values: list[RiskResponse]) -> "Totals":
        amounts: dict[str, str | None] = {}
        for field in cls.model_fields:
            entries: list[str | None] = [getattr(item, field) for item in values]
            # Missing valuation must not become a misleading zero or partial total.
            amounts[field] = (
                str(total(Decimal(value) for value in entries if value is not None))
                if entries and all(value is not None for value in entries)
                else None
            )
        return cls(**amounts)


class WorkspaceResponse(BaseModel):
    instance_id: str
    run_id: str | None
    revision: int
    server_time_ms: int
    system: SystemResponse
    accounts: list[AccountResponse]
    instruments: list[InstrumentResponse]
    positions: list[PositionResponse]
    risks: list[RiskResponse]
    market: MarketResponse | None
    sources: SourcesResponse | None
    settlement: SessionStatus | None
    settlement_days: list[SettlementDayResponse]
    automatic_settlement: bool
    capabilities: Capabilities
    totals: Totals


def empty_workspace(request: Request, system: SystemResponse) -> WorkspaceResponse:
    return WorkspaceResponse(
        instance_id=request.app.state.instance_id,
        run_id=None,
        revision=0,
        server_time_ms=time.time_ns() // 1_000_000,
        system=system,
        accounts=[],
        instruments=[],
        positions=[],
        risks=[],
        market=None,
        sources=None,
        settlement=None,
        settlement_days=[],
        automatic_settlement=False,
        capabilities=Capabilities(
            ledger=False, trading=False, manual_market=False, multi_source=False, settlement=False
        ),
        totals=Totals.from_risks([]),
    )


@router.get("/workspace", operation_id="getWorkspace")
async def workspace(request: Request) -> WorkspaceResponse:
    # Call the in-memory queries directly: their async bodies contain no suspending
    # I/O. Do not use Depends(Service), gather, to_thread or a sync route here:
    # all fields must observe the same committed version on the service's loop.
    service = request.app.state.ledger
    system = system_status(request)
    result = empty_workspace(request, system)
    if not isinstance(service, LedgerService):
        return result
    try:
        if not service.available:
            raise LedgerUnavailable("账本服务未就绪或存储不可用")
        result.accounts = await accounts(service)
        result.positions = await positions(service)
        result.instruments = [
            InstrumentResponse(
                instrument_id=item.instrument_id,
                product_id=item.product_id,
                multiplier=item.multiplier,
                tick_size=str(item.tick_size),
                margin_rate=str(item.margin_rate),
                fee_per_lot=str(item.fee_per_lot),
            )
            for item in service.definition.instruments
        ]
        result.run_id = service.definition.run_id
        result.revision = sum(item.revision for item in result.accounts)
        result.capabilities = Capabilities(
            ledger=True,
            trading=service.trading_enabled,
            manual_market=service.trading_enabled and service.market_plan is None,
            multi_source=service.market_plan is not None,
            settlement=service.settlement_plan is not None,
        )
        if service.trading_enabled:
            result.revision = service.trading_state().revision
            result.market = await market(service)
            result.risks = await risk(service)
            result.totals = Totals.from_risks(result.risks)
        if service.market_plan is not None:
            result.sources = await sources(service)
        if service.settlement_plan is not None:
            worker: SettlementScheduler = request.app.state.settlement_scheduler
            result.settlement = await status(service, worker)
            result.automatic_settlement = worker.settings.auto_settle
            result.settlement_days = [
                SettlementDayResponse(
                    trading_day=str(item.session.trading_day),
                    close_sequence=item.session.close_sequence,
                    prices={key: str(value) for key, value in item.prices},
                )
                for item in service.settlement_plan.visible_days(
                    service.trading_state().trading_day
                )
            ]
        return result
    except LedgerUnavailable:
        unavailable = system.model_copy(
            update={
                "ledger_available": False,
                "trading_available": False,
                "reasons": ["账本服务未就绪或存储不可用；工作区数据不可用。"],
            }
        )
        return empty_workspace(request, unavailable)
