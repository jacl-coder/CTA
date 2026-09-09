from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"
    version: str


class SystemResponse(BaseModel):
    version: str
    stage: Literal["foundation", "ledger"] = "foundation"
    ledger_available: bool = False
    trading_available: bool
    reasons: list[str]
