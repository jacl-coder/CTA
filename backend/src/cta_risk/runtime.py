"""单个场景的资源生命周期，退出时先停调度与行情，再关闭账本。"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from cta_risk.application.ledger import LedgerService
from cta_risk.config.models import Settings
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler
from cta_risk.simulator.launcher import SourceProcesses
from cta_risk.storage.sqlite import SQLiteLedgerStore


@asynccontextmanager
async def running(app: FastAPI, settings: Settings) -> AsyncIterator[None]:
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
