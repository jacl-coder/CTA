"""独立只读 HTTP 模拟源：历史由冻结场景与起点重建，连接中断不停止逻辑时间。"""

import asyncio
import os
import signal
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Query

from cta_risk.config.models import Settings
from cta_risk.domain.market import generate_frame
from cta_risk.marketdata.protocol import HeadResponse, HistoryResponse, WireFrame


def create_source_app(
    settings: Settings,
    source_id: str,
    epoch_ms: int,
    clock: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    parent_pid: int | None = None,
) -> FastAPI:
    if settings.ledger is None or settings.market is None:
        raise ValueError("缺少行情与账本配置")
    if epoch_ms < 0:
        raise ValueError("行情起点非法")
    plan = settings.market.plan(settings.ledger)
    source = next(item for item in plan.sources if item.source_id == source_id)
    config = next(item for item in settings.market.sources if item.source_id == source_id)

    async def watch_parent() -> None:
        while parent_pid is not None:
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            await asyncio.sleep(0.2)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(watch_parent()) if parent_pid is not None else None
        try:
            yield
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher

    app = FastAPI(title=f"CTA 模拟行情源 {source_id}", lifespan=lifespan)

    async def current() -> int:
        if config.delay_ms:
            await asyncio.sleep(config.delay_ms / 1000)
        now = clock()
        sequence = plan.sequence_at(epoch_ms, now)
        if not plan.completed(epoch_ms, now) and any(
            item.start <= sequence <= item.end for item in config.outages
        ):
            raise HTTPException(503, "模拟连接中断，逻辑行情仍在推进")
        return sequence

    @app.get("/head")
    async def head() -> HeadResponse:
        sequence = await current()
        return HeadResponse(
            run_id=plan.run_id,
            source_id=source_id,
            epoch_ms=epoch_ms,
            sequence=sequence,
            complete=plan.completed(epoch_ms, clock()),
            latest=WireFrame.from_domain(generate_frame(plan, source, epoch_ms, sequence))
            if sequence
            else None,
        )

    @app.get("/history")
    async def history(
        start: int = Query(ge=1), end: int = Query(ge=1), limit: int = Query(64, ge=1, le=256)
    ) -> HistoryResponse:
        sequence = await current()
        if end < start or end > sequence:
            raise HTTPException(409, "请求历史范围尚不可用或顺序非法")
        last = min(end, start + limit - 1)
        return HistoryResponse(
            run_id=plan.run_id,
            source_id=source_id,
            epoch_ms=epoch_ms,
            start=start,
            end=last,
            head=sequence,
            next_sequence=last + 1,
            frames=[
                WireFrame.from_domain(generate_frame(plan, source, epoch_ms, i))
                for i in range(start, last + 1)
            ],
        )

    return app
