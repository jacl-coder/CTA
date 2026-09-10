from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from cta_risk import __version__
from cta_risk.api.schemas import LivenessResponse, SystemResponse
from cta_risk.application.ledger import LedgerService
from cta_risk.application.status import get_system_status

router = APIRouter(prefix="/api")


@router.get("/health/live", operation_id="getLiveness", tags=["health"])
def liveness() -> LivenessResponse:
    return LivenessResponse(version=__version__)


@router.get("/system", operation_id="getSystemStatus", tags=["system"])
def system_status(request: Request) -> SystemResponse:
    status = get_system_status()
    service = request.app.state.ledger
    available = isinstance(service, LedgerService) and service.available
    if isinstance(service, LedgerService) and available and service.trading_enabled:
        return SystemResponse(
            version=__version__,
            stage="trading",
            ledger_available=True,
            trading_available=service.trading_available,
            reasons=[]
            if service.trading_available
            else [
                f"交易日 {service.trading_state().trading_day} 已封账或清算，当前不能交易。"
                if service.trading_state().phase != "OPEN"
                else str(service.source_status()["reason"])
                if service.market_plan is not None
                else "等待完整且有效的模拟行情；价格缺失、过期或进度异常时暂停交易。"
            ],
        )
    return SystemResponse(
        version=__version__,
        trading_available=status.trading_available,
        ledger_available=available,
        stage="ledger" if available else "foundation",
        reasons=["账户账本已恢复，行情与风控引擎尚未接入，当前不能交易。"]
        if available
        else list(status.reasons),
    )


@router.get(
    "/health/ready",
    operation_id="getReadiness",
    response_model=SystemResponse,
    responses={503: {"model": SystemResponse, "description": "交易服务尚未就绪"}},
    tags=["health"],
)
def readiness(request: Request) -> JSONResponse:
    result = system_status(request)
    return JSONResponse(result.model_dump(), status_code=200 if result.trading_available else 503)
