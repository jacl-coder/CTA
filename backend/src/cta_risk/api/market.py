"""行情连接、接收游标与应用游标查询。"""

from fastapi import APIRouter
from pydantic import BaseModel

from cta_risk.api.ledger import Service

router = APIRouter(prefix="/api", tags=["market"])


class SourceResponse(BaseModel):
    source_id: str
    connected: bool
    head: int
    contiguous_sequence: int
    received_count: int
    reason: str


class SourcesResponse(BaseModel):
    epoch_ms: int
    applied_sequence: int
    expected_sequence: int
    market_ready: bool
    completed: bool
    reason: str
    duplicate_frames: int
    sources: list[SourceResponse]


@router.get("/market/sources", operation_id="getMarketSources")
async def sources(service: Service) -> SourcesResponse:
    return SourcesResponse.model_validate(service.source_status())
