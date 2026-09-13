"""工作区能力、精确金额、同版本快照与冻结日报的 HTTP 契约。"""

import json
import re
import sqlite3
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import yaml
from fastapi import Request
from pydantic import ValidationError

from cta_risk.api.settlement import DailyReportResponse
from cta_risk.api.workspace import WorkspaceResponse, workspace
from cta_risk.application.contracts import LedgerUnavailable
from cta_risk.bootstrap import create_app
from cta_risk.config.loader import load_settings
from cta_risk.config.models import Settings
from cta_risk.config.settlement import SettlementDaySettings, SettlementSettings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.models import Offset, Side
from cta_risk.domain.settlement import DayCommand
from cta_risk.domain.trading import Order, PriceFrame
from cta_risk.marketdata.client import MarketFeed
from cta_risk.settlement.scheduler import SettlementScheduler

pytestmark = pytest.mark.asyncio
DAY = date(2026, 9, 9)
NEXT_DAY = date(2026, 9, 10)
PRICES = {"SHFE.rb2610": "3510", "DCE.i2701": "810", "CFFEX.IF2609": "4005"}


def frame(sequence: int = 1, day: date = DAY) -> PriceFrame:
    return PriceFrame(
        sequence,
        day,
        (
            ("CFFEX.IF2609", Decimal("4005.2")),
            ("DCE.i2701", Decimal("810.5")),
            ("SHFE.rb2610", Decimal("3350")),
        ),
    )


@pytest.fixture
def trading_settings(demo_settings: Settings) -> Settings:
    return Settings(
        ledger=demo_settings.ledger,
        trading=TradingSettings(default_product_limit="2000000"),
    )


@pytest.fixture
def settlement_settings(trading_settings: Settings, tmp_path: Path) -> Settings:
    return Settings(
        ledger=trading_settings.ledger,
        trading=trading_settings.trading,
        settlement=SettlementSettings(
            auto_settle=False,
            report_dir=tmp_path / "reports",
            days=tuple(
                SettlementDaySettings(
                    trading_day=str(day), close_sequence=index, prices=PRICES.copy()
                )
                for index, day in enumerate((DAY, NEXT_DAY), 1)
            ),
        ),
    )


@pytest.mark.parametrize(
    "mode", ["foundation", "ledger", "trading", "settlement", "multi_source", "automatic"]
)
async def test_workspace_modes_and_no_suspension(
    mode: str,
    demo_settings: Settings,
    trading_settings: Settings,
    settlement_settings: Settings,
    tmp_path: Path,
    unused_tcp_port_factory: Callable[[], int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "foundation": Settings(),
        "ledger": demo_settings,
        "trading": trading_settings,
        "settlement": settlement_settings,
    }.get(mode)
    if settings is None:
        root = Path(__file__).resolve().parents[3]
        config = "settlement-demo.yaml" if mode == "automatic" else "market-demo.yaml"
        raw = yaml.safe_load((root / "testdata/configs" / config).read_text())
        raw["ledger"]["database"] = "workspace.sqlite3"
        raw["market"]["managed_sources"] = False
        for source in raw["market"]["sources"]:
            source["port"] = unused_tcp_port_factory()
        if "settlement" in raw:
            raw["settlement"]["report_dir"] = "reports"
        path = tmp_path / "workspace.yaml"
        path.write_text(yaml.safe_dump(raw))
        settings = load_settings(path)
    # The HTTP contract needs real persisted services, but no network listeners
    # or scheduler timing. Source availability initially remains explicitly false.
    monkeypatch.setattr(MarketFeed, "start", AsyncMock())
    monkeypatch.setattr(SettlementScheduler, "start", AsyncMock())
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        before_ms = time.time_ns() // 1_000_000
        response = await client.get("/api/workspace")
        assert response.status_code == 200
        body = response.json()
        assert before_ms <= body["server_time_ms"] <= time.time_ns() // 1_000_000
        assert re.fullmatch(r"[0-9a-f]{16}", body["instance_id"])
        ledger = mode != "foundation"
        trading = mode not in {"foundation", "ledger"}
        multi_source = mode in {"multi_source", "automatic"}
        settlement = mode in {"settlement", "automatic"}
        assert body["capabilities"] == {
            "ledger": ledger,
            "trading": trading,
            "manual_market": trading and not multi_source,
            "multi_source": multi_source,
            "settlement": settlement,
        }
        assert body["automatic_settlement"] is (mode == "automatic")
        assert (body["sources"] is not None) is multi_source
        assert (body["settlement"] is not None) is settlement
        assert (body["market"] is not None) is trading
        assert body["system"] == (await client.get("/api/system")).json()
        assert body["system"]["ledger_available"] is ledger
        assert not body["system"]["trading_available"]
        assert all(value is None for value in body["totals"].values())
        if ledger:
            service = app.state.ledger
            assert body["run_id"] == service.definition.run_id
            assert body["accounts"] == (await client.get("/api/accounts")).json()
            assert body["positions"] == (await client.get("/api/positions")).json()
            assert body["revision"] == (
                service.trading_state().revision
                if trading
                else sum(item.revision for item in service.accounts())
            )
            assert len(body["accounts"]) == len(body["instruments"]) == 3
            assert body["instruments"][0] == {
                "instrument_id": "SHFE.rb2610",
                "product_id": "SHFE.rb",
                "multiplier": 10,
                "tick_size": "1",
                "margin_rate": "0.10",
                "fee_per_lot": "2",
            }
        else:
            assert body["run_id"] is None and body["revision"] == 0
            assert body["accounts"] == body["positions"] == body["instruments"] == []
        if trading:
            assert body["risks"] == (await client.get("/api/risk")).json()
            assert body["market"] == (await client.get("/api/market")).json()
            assert all(item["equity"] is None for item in body["risks"])
            assert all(not item["market_ready"] for item in body["risks"])
        else:
            assert body["risks"] == []
        if multi_source:
            assert body["sources"] == (await client.get("/api/market/sources")).json()
        if settlement:
            assert body["settlement"] == (await client.get("/api/settlement/status")).json()
            assert body["settlement"]["phase"] == "OPEN"
            assert body["settlement_days"] == [
                {
                    "trading_day": str(day.session.trading_day),
                    "close_sequence": day.session.close_sequence,
                    "prices": {key: str(value) for key, value in day.prices},
                }
                for day in app.state.ledger.settlement_plan.days
            ]
        else:
            assert body["settlement_days"] == []
        assert "token" not in response.text.lower()
        if app.state.write_token:
            assert app.state.write_token not in response.text
        assert WorkspaceResponse.model_validate_json(response.text).model_dump(mode="json") == body

        # A single coroutine step must complete every read, including nested async
        # queries. Any future await that yields would permit a mixed-version snapshot.
        request = Request({"type": "http", "app": app})
        pending = workspace(request)
        try:
            with pytest.raises(StopIteration) as finished:
                pending.send(None)
            assert isinstance(finished.value.value, WorkspaceResponse)
            assert finished.value.value.revision == body["revision"]
        finally:
            pending.close()


async def test_totals_revision_and_historical_market_after_restart(
    trading_settings: Settings,
) -> None:
    app = create_app(trading_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        service = app.state.ledger
        await service.publish_frame(frame())
        # Aggregation uses the backend financial context even if the caller has a
        # low Decimal precision. Float or default-context sums would lose cents.
        with localcontext() as context:
            context.prec = 6
            response = await client.get("/api/workspace")
        body = response.json()
        assert response.status_code == 200
        assert body["totals"] == {
            "equity": "1296541.00",
            "floating_pnl": "-3540.00",
            "margin": "170339.20",
            "available_funds": "1126201.80",
            "gross_exposure": "1430660.00",
        }
        assert body["market"]["market_ready"]
        assert body["risks"][0]["circuit_broken"]
        assert body["risks"][0]["blocking_reasons"] == ["CIRCUIT_BROKEN"]
        assert all(item["market_ready"] for item in body["risks"])
        revision = body["revision"]
        # A rejected order advances trading revision without changing account books.
        order = Order("blocked", "A", "SHFE.rb2610", DAY, Side.LONG, Offset.OPEN, 1)
        assert (await service.submit_order(order)).status == "REJECTED"
        latest = (await client.get("/api/workspace")).json()
        assert latest["revision"] == revision + 1 == service.trading_state().revision
        assert latest["accounts"] == body["accounts"]
        assert latest["instance_id"] == body["instance_id"]
    # A new lifespan, including on the same app object, gets a new public identity.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        restored = (await client.get("/api/workspace")).json()
        assert restored["instance_id"] != body["instance_id"]
        assert restored["run_id"] == body["run_id"]
        assert restored["revision"] == latest["revision"]
        assert restored["totals"] == body["totals"]
        assert restored["market"]["prices"] == body["market"]["prices"]
        assert restored["capabilities"] == body["capabilities"]
        assert not restored["market"]["market_ready"]
        assert not restored["system"]["trading_available"]
        assert all(not item["market_ready"] for item in restored["risks"])
        assert all("MARKET_UNAVAILABLE" in item["blocking_reasons"] for item in restored["risks"])


async def test_storage_failure_returns_unavailable_empty_workspace(
    trading_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(trading_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        service = app.state.ledger
        await service.publish_frame(frame())
        monkeypatch.setattr(
            service._store, "save_transition", Mock(side_effect=sqlite3.OperationalError("disk"))
        )
        with pytest.raises(LedgerUnavailable):
            await service.publish_frame(frame(2))
        response = await client.get("/api/workspace")
        assert response.status_code == 200
        body = response.json()
        assert not body["system"]["ledger_available"]
        assert not body["system"]["trading_available"]
        assert "存储不可用" in " ".join(body["system"]["reasons"])
        assert not any(body["capabilities"].values())
        assert body["accounts"] == body["positions"] == body["risks"] == []
        assert body["instruments"] == body["settlement_days"] == []
        assert body["market"] is body["sources"] is body["settlement"] is None
        assert body["run_id"] is None and body["revision"] == 0
        assert not body["automatic_settlement"]
        assert all(value is None for value in body["totals"].values())
        assert app.state.write_token not in response.text


async def test_typed_report_preserves_frozen_json_and_rollover_phase(
    settlement_settings: Settings,
) -> None:
    app = create_app(settlement_settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        assert (await client.get(f"/api/reports/{DAY}")).status_code == 404
        assert (await client.get("/api/reports/not-a-day")).status_code == 422
        service = app.state.ledger
        await service.publish_frame(frame())
        await service.day_command(DayCommand("close", DAY))
        closing = (await client.get("/api/workspace")).json()
        assert closing["settlement"]["phase"] == "CLOSING"
        assert not closing["market"]["market_ready"]
        assert closing["risks"][0]["circuit_broken"]
        prices = tuple(sorted((key, Decimal(value)) for key, value in PRICES.items()))
        await service.day_command(DayCommand("settle", DAY, prices))
        payload = service.trading_state().reports[0]
        response = await client.get(f"/api/reports/{DAY}")
        assert response.status_code == 200
        report = response.json()
        assert report == json.loads(payload)
        assert DailyReportResponse.model_validate_json(payload).model_dump(mode="json") == report
        assert report["totals"]["closing_balance"] == "1299781.00"
        assert report["accounts"][0]["risk"]["circuit_broken"]
        assert report["accounts"][0]["settlement"]["lines"][0]["pnl"] == "200.00"
        assert report["accounts"][0]["valuation"]["positions"][0]["notional"] == "70200"
        assert report["prices"] == PRICES
        assert (await client.get("/api/workspace")).json()["settlement"]["phase"] == "SETTLED"
        await service.day_command(DayCommand("open_day", NEXT_DAY))
        opened = (await client.get("/api/workspace")).json()
        assert opened["settlement"]["phase"] == "OPEN"
        assert opened["settlement"]["settled_days"] == [str(DAY)]
        assert not opened["settlement"]["market_ready"]
        assert opened["market"]["trading_day"] == str(DAY)
        assert not opened["market"]["market_ready"]
        assert all(value is None for value in opened["totals"].values())
        assert all(not item["circuit_broken"] for item in opened["risks"])
        assert (await client.get(f"/api/reports/{DAY}")).json() == report
        assert service.trading_state().reports[0] == payload

        # String amounts beyond JS's safe integer range stay byte-for-byte exact;
        # accepting JSON numbers here would let a float silently corrupt the contract.
        report["totals"]["closing_balance"] = "9007199254740993.01"
        typed = DailyReportResponse.model_validate_json(json.dumps(report))
        assert typed.model_dump(mode="json")["totals"]["closing_balance"] == "9007199254740993.01"
        report["totals"]["closing_balance"] = 0.1
        with pytest.raises(ValidationError):
            DailyReportResponse.model_validate_json(json.dumps(report))


async def test_openapi_exposes_named_typed_read_contracts() -> None:
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        document = (await client.get("/api/openapi.json")).json()
    schemas = document["components"]["schemas"]
    for path, name in (
        ("/api/workspace", "WorkspaceResponse"),
        ("/api/reports/{trading_day}", "DailyReportResponse"),
    ):
        operation = document["paths"][path]["get"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{name}"
        }
        assert not operation.get("security")
        assert not any(p["in"] == "header" for p in operation.get("parameters", []))
    assert schemas["DailyReportResponse"]["properties"]["accounts"]["items"] == {
        "$ref": "#/components/schemas/ReportAccountResponse"
    }
    assert schemas["ReportAccountResponse"]["properties"]["risk"] == {
        "$ref": "#/components/schemas/ReportRiskResponse"
    }
    assert schemas["SettlementResultResponse"]["properties"]["closing_balance"] == {
        "type": "string",
        "title": "Closing Balance",
    }
    for name in ("WorkspaceResponse", "Capabilities", "Totals"):
        # Scene controls are optional for legacy/specialized acceptance configurations.
        optional = {"demo", "policy", "risk_comparison"} if name == "WorkspaceResponse" else set()
        assert set(schemas[name]["required"]) == set(schemas[name]["properties"]) - optional
    assert "JsonValue" not in json.dumps(schemas["DailyReportResponse"])
