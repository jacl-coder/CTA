from pathlib import Path

import httpx
import pytest

from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings


@pytest.mark.asyncio
async def test_liveness_does_not_imply_trading_readiness() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        assert (await client.get("/api/health/live")).status_code == 200
        result = await client.get("/api/health/ready")
        assert result.status_code == 503
        assert result.json()["trading_available"] is False
        assert result.json()["reasons"]


@pytest.mark.asyncio
async def test_spa_fallback_does_not_mask_missing_api_or_assets(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>CTA</h1>")
    (tmp_path / "secret.txt").write_text("outside static root")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(Settings(frontend_dir=static))),
        base_url="http://test",
    ) as client:
        assert (await client.get("/accounts")).text == "<h1>CTA</h1>"
        for path in ("/api/missing", "/assets/missing.js", "/%2e%2e/secret.txt"):
            assert (await client.get(path)).status_code == 404
        assert (await client.get("/api/system")).headers["content-type"] == "application/json"
