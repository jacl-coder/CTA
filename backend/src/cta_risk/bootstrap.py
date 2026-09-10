"""组合根：装配适配层；创建应用不启动后台任务或修改数据库。"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cta_risk import __version__
from cta_risk.api.ledger import router as ledger_router
from cta_risk.api.market import router as market_router
from cta_risk.api.routes import router
from cta_risk.api.settlement import router as settlement_router
from cta_risk.api.static import mount_frontend
from cta_risk.api.trading import router as trading_router
from cta_risk.api.workspace import router as workspace_router
from cta_risk.application.contracts import AccountNotFound, LedgerBusy, LedgerUnavailable
from cta_risk.application.ledger import LedgerService
from cta_risk.config.models import Settings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.trading import CommandConflict
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler
from cta_risk.simulator.launcher import SourceProcesses
from cta_risk.storage.sqlite import SQLiteLedgerStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.instance_id = secrets.token_hex(8)
        service = None
        feed = None
        sources = None
        settlement = None
        if settings.ledger is not None:
            config = settings.ledger
            service = LedgerService(
                config.definition(),
                SQLiteLedgerStore(config.database),
                config.queue_capacity,
                policy=settings.trading.policy() if settings.trading is not None else None,
                market_plan=settings.market.plan(config) if settings.market is not None else None,
                settlement_plan=settings.settlement.plan(config) if settings.settlement else None,
            )
            await service.start()
        app.state.ledger = service
        app.state.write_token = secrets.token_urlsafe(32) if settings.trading is not None else None
        try:
            if service is not None and settings.market is not None:
                if settings.market.managed_sources:
                    sources = SourceProcesses(settings, service.source_state().epoch_ms)
                    await sources.start()
                feed = MarketFeed(service, settings.market)
                await feed.start()
            if service is not None and settings.settlement is not None:
                settlement = SettlementScheduler(service, settings.settlement)
                app.state.settlement_scheduler = settlement
                await settlement.start()
            yield
        finally:
            if settlement is not None:
                await settlement.close()
            if feed is not None:
                await feed.close()
            if sources is not None:
                await sources.close()
            if service is not None:
                await service.close()
            app.state.ledger = None
            app.state.write_token = None
            app.state.settlement_scheduler = None

    app = FastAPI(
        title="CTA 期货风控 API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.ledger = None
    app.state.write_token = None
    app.state.settlement_scheduler = None

    async def business_error(request: Request, exc: Exception) -> JSONResponse:
        status = 422
        if isinstance(exc, CommandConflict):
            status = 409
        elif isinstance(exc, LedgerBusy):
            status = 429
        elif isinstance(exc, LedgerUnavailable):
            status = 503
        elif isinstance(exc, AccountNotFound):
            status = 404
        return JSONResponse({"detail": str(exc)}, status_code=status)

    for error in (AccountingError, LedgerBusy, LedgerUnavailable, AccountNotFound):
        app.add_exception_handler(error, business_error)
    app.include_router(router)
    app.include_router(ledger_router)
    app.include_router(trading_router)
    app.include_router(market_router)
    app.include_router(settlement_router)
    app.include_router(workspace_router)
    packaged = Path(__file__).resolve().parent / "static"
    if settings.frontend_dir is not None:
        mount_frontend(app, settings.frontend_dir)
    elif packaged.is_dir():
        mount_frontend(app, packaged)
    return app
