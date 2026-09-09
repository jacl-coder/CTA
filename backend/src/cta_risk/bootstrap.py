"""组合根：装配适配层；创建应用不启动后台任务或修改数据库。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from cta_risk import __version__
from cta_risk.api.ledger import router as ledger_router
from cta_risk.api.routes import router
from cta_risk.api.static import mount_frontend
from cta_risk.application.ledger import LedgerService
from cta_risk.config.models import Settings
from cta_risk.storage.sqlite import SQLiteLedgerStore


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = None
        if settings.ledger is not None:
            config = settings.ledger
            service = LedgerService(
                config.definition(), SQLiteLedgerStore(config.database), config.queue_capacity
            )
            await service.start()
        app.state.ledger = service
        try:
            yield
        finally:
            if service is not None:
                await service.close()
            app.state.ledger = None

    app = FastAPI(
        title="CTA 期货风控 API",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.ledger = None
    app.include_router(router)
    app.include_router(ledger_router)
    packaged = Path(__file__).resolve().parent / "static"
    if settings.frontend_dir is not None:
        mount_frontend(app, settings.frontend_dir)
    elif packaged.is_dir():
        mount_frontend(app, packaged)
    return app
