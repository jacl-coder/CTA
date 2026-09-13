"""同一地址内切换演示场景，写请求必须绑定页面看到的运行。"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from cta_risk.api.security import require_session
from cta_risk.api.trading import StrictInput
from cta_risk.demonstration import DemoController, DemoStatus, Mode
from cta_risk.domain.trading import CommandConflict

router = APIRouter(prefix="/api/demo", tags=["demo"])


class SwitchInput(StrictInput):
    mode: Mode


class ScenarioGuard(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        controller = getattr(request.app.state, "demo", None)
        if not isinstance(controller, DemoController) or not request.url.path.startswith("/api/"):
            return await call_next(request)
        if controller.switching:
            return JSONResponse({"detail": "正在切换场景，请稍后刷新"}, status_code=503)
        async with controller.lock:
            # GET workspace/session deliberately omit the run binding so an old tab
            # can discover the new state. Business reads can opt in; writes must bind.
            scope = request.headers.get("x-cta-run-id")
            if request.method == "POST" or (
                scope
                and request.url.path
                not in {
                    "/api/workspace",
                    "/api/session",
                }
            ):
                try:
                    controller.check_run(scope)
                except CommandConflict as exc:
                    return JSONResponse({"detail": str(exc)}, status_code=409)
            return await call_next(request)


@router.post("/switch", operation_id="switchDemo", dependencies=[Depends(require_session)])
async def switch(body: SwitchInput, request: Request) -> DemoStatus:
    controller = getattr(request.app.state, "demo", None)
    if not isinstance(controller, DemoController):
        raise HTTPException(404, "当前配置未启用场景选择")
    # A disconnected browser must not cancel resource shutdown halfway through.
    task = asyncio.create_task(controller.switch(body.mode))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).exception("场景切换失败")
        raise HTTPException(503, "场景启动失败，已尝试恢复原场景；请检查服务日志") from exc
    return controller.status()
