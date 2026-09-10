"""日结操作与冻结日报查询，沿用本地会话写权限。"""

import json
from dataclasses import asdict
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from cta_risk.api.ledger import Service
from cta_risk.api.security import require_session
from cta_risk.api.trading import StrictInput, parse_day
from cta_risk.domain.models import Side
from cta_risk.domain.numbers import decimal_value
from cta_risk.domain.settlement import DayCommand
from cta_risk.settlement.scheduler import SettlementScheduler

router = APIRouter(prefix="/api", tags=["settlement"])


class DayInput(StrictInput):
    trading_day: str


class SettleInput(DayInput):
    prices: dict[str, str]


class DayResponse(BaseModel):
    trading_day: str
    phase: str
    duplicate: bool


class SessionStatus(BaseModel):
    trading_day: str
    phase: str
    close_sequence: int
    applied_sequence: int
    market_ready: bool
    settled_days: list[str]
    report_errors: dict[str, str]


class SettlementLineResponse(BaseModel):
    lot_id: str
    instrument_id: str
    side: Side
    quantity: int
    basis: str
    settlement_price: str
    pnl: str


class SettlementAmountsResponse(BaseModel):
    opening_balance: str
    realized_pnl: str
    holding_pnl: str
    fees: str
    net_pnl: str
    closing_balance: str
    margin: str
    available: str


class SettlementResultResponse(SettlementAmountsResponse):
    account_id: str
    trading_day: str
    prices: list[tuple[str, str]]
    lines: list[SettlementLineResponse]


class ReportPositionResponse(BaseModel):
    account_id: str
    instrument_id: str
    product_id: str
    side: Side
    quantity: int
    notional: str
    margin: str
    floating_pnl: str


class ReportValuationResponse(BaseModel):
    positions: list[ReportPositionResponse]
    floating_pnl: str
    equity: str
    margin: str
    available: str
    gross_exposure: str
    net_exposure: str


class ReportRiskResponse(BaseModel):
    account_id: str
    warning: bool
    circuit_broken: bool
    restricted_products: list[str]


class ReportAccountResponse(BaseModel):
    account_id: str
    initial_capital: str
    settlement: SettlementResultResponse
    valuation: ReportValuationResponse
    risk: ReportRiskResponse
    risk_event_count: int


class ReportTotalsResponse(SettlementAmountsResponse):
    gross_exposure: str
    net_exposure: str


class ReportMetadataResponse(BaseModel):
    run_id: str
    trading_day: str
    close_sequence: int
    config_hash: str
    policy_hash: str
    settlement_plan_hash: str


class DailyReportResponse(ReportMetadataResponse):
    prices: dict[str, str]
    accounts: list[ReportAccountResponse]
    totals: ReportTotalsResponse


def scheduler(request: Request) -> SettlementScheduler:
    result = getattr(request.app.state, "settlement_scheduler", None)
    if not isinstance(result, SettlementScheduler):
        raise HTTPException(503, "未启用日结流程")
    return result


Scheduler = Annotated[SettlementScheduler, Depends(scheduler)]


@router.get("/settlement/status", operation_id="getSettlementStatus")
async def status(service: Service, worker: Scheduler) -> SessionStatus:
    return SessionStatus.model_validate(
        {**service.session_status(), "report_errors": worker.errors}
    )


async def submit(service: Service, command: DayCommand) -> DayResponse:
    result = await service.day_command(command)
    return DayResponse(**{**asdict(result), "trading_day": str(result.trading_day)})


@router.post(
    "/settlement/close", operation_id="closeTradingDay", dependencies=[Depends(require_session)]
)
async def close_day(body: DayInput, service: Service) -> DayResponse:
    return await submit(service, DayCommand("close", parse_day(body.trading_day)))


@router.post(
    "/settlement/settle", operation_id="settleTradingDay", dependencies=[Depends(require_session)]
)
async def settle(body: SettleInput, service: Service) -> DayResponse:
    return await submit(
        service,
        DayCommand(
            "settle",
            parse_day(body.trading_day),
            tuple(sorted((key, decimal_value(value)) for key, value in body.prices.items())),
        ),
    )


@router.post(
    "/settlement/open", operation_id="openNextTradingDay", dependencies=[Depends(require_session)]
)
async def open_day(body: DayInput, service: Service) -> DayResponse:
    return await submit(service, DayCommand("open_day", parse_day(body.trading_day)))


def report_payload(service: Service, day: str) -> str:
    parsed = str(parse_day(day))
    for payload in service.trading_state().reports:
        if json.loads(payload)["trading_day"] == parsed:
            return payload
    raise HTTPException(404, "该交易日尚无已提交的日报")


@router.get("/reports", operation_id="listDailyReports")
async def reports(service: Service) -> list[str]:
    return [json.loads(payload)["trading_day"] for payload in service.trading_state().reports]


@router.get("/reports/{trading_day}", operation_id="getDailyReport")
async def report(trading_day: str, service: Service) -> DailyReportResponse:
    return DailyReportResponse.model_validate_json(report_payload(service, trading_day))


@router.post(
    "/reports/{trading_day}/export",
    operation_id="exportDailyReport",
    dependencies=[Depends(require_session)],
)
async def export(trading_day: str, service: Service, worker: Scheduler) -> dict[str, str]:
    try:
        html, data = await worker.export(report_payload(service, trading_day))
    except OSError as exc:
        raise HTTPException(503, "日报文件写入失败；清算已保留，可重试导出") from exc
    return {"html": html.name, "json": data.name}


@router.get("/reports/{trading_day}/download/{format}", operation_id="downloadDailyReport")
async def download(
    trading_day: str, format: Literal["html", "json"], service: Service, worker: Scheduler
) -> FileResponse:
    report_payload(service, trading_day)
    path = worker.settings.report_dir / f"risk-{str(parse_day(trading_day))}.{format}"
    if not path.is_file():
        raise HTTPException(503, "日报文件尚未导出，请重试导出")
    return FileResponse(
        path, media_type="text/html" if format == "html" else "application/json", filename=path.name
    )
