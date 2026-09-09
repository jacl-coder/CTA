"""仅声明已经由工程骨架使用的配置，业务参数随模块实现增加。"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cta_risk.config.ledger import LedgerSettings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ServerSettings(StrictModel):
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)


class Settings(StrictModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    # Explicit resource path override; absence uses packaged frontend resources.
    frontend_dir: Path | None = None
    ledger: LedgerSettings | None = None
