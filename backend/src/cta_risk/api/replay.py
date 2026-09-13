"""历史回放入口；计算使用独立状态，不调用在线下单或日结接口。"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from cta_risk.api.ledger import Service
from cta_risk.api.security import require_session
from cta_risk.replay.engine import ReplayComparison, compare
from cta_risk.replay.export import export_current
from cta_risk.replay.models import MAX_BYTES, ReplayDataset, ReplayRequest

router = APIRouter(prefix="/api/replay", tags=["replay"])


@router.get("/example", operation_id="getReplayExample")
async def example() -> ReplayDataset:
    path = Path(__file__).resolve().parent.parent / "replay/sample.json"
    return ReplayDataset.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/current", operation_id="exportReplayHistory")
async def current(service: Service) -> ReplayDataset:
    return await export_current(service)


@router.post("/run", operation_id="runReplay", dependencies=[Depends(require_session)])
async def replay(body: ReplayRequest, request: Request) -> ReplayComparison:
    if len(body.model_dump_json().encode()) > MAX_BYTES:
        raise HTTPException(413, "历史输入不能超过 8 MiB")
    lock: asyncio.Lock = request.app.state.replay_lock
    if lock.locked():
        raise HTTPException(429, "已有回放正在计算，请稍后重试")
    async with lock:
        task = asyncio.create_task(asyncio.to_thread(compare, body))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        except (ValueError, LookupError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post(
    "/validate", operation_id="validateReplayDataset", dependencies=[Depends(require_session)]
)
async def validate(body: ReplayDataset) -> ReplayDataset:
    if len(body.model_dump_json().encode()) > MAX_BYTES:
        raise HTTPException(413, "历史输入不能超过 8 MiB")
    try:
        ledger = body.seed.ledger()
        body.plan(ledger)
        body.commands()
    except (ValueError, LookupError, ArithmeticError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return body
