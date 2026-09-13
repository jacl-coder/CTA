"""附加回放：手算、规则差异、历史可移植性，以及在线账本与回放隔离。"""

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from cta_risk.application.trading import OrderCommand
from cta_risk.bootstrap import create_app
from cta_risk.config.models import Settings
from cta_risk.config.settlement import SettlementSettings
from cta_risk.config.trading import TradingSettings
from cta_risk.domain.errors import AccountingError
from cta_risk.domain.settlement import DayCommand
from cta_risk.domain.trading import PriceFrame
from cta_risk.replay.engine import compare, state_digest
from cta_risk.replay.models import ReplayDataset, ReplayRequest

SAMPLE = Path(__file__).resolve().parents[2] / "src/cta_risk/replay/sample.json"


def sample() -> ReplayDataset:
    return ReplayDataset.model_validate_json(SAMPLE.read_text())


def test_two_day_replay_rules_and_hand_calculated_balances() -> None:
    dataset = sample()
    candidate = TradingSettings.model_validate(
        {**dataset.policy.model_dump(), "loss_ratio": "0.05"}
    )
    result = compare(ReplayRequest(dataset=dataset, candidate_policy=candidate))
    base, other = result.baseline, result.candidate
    assert not base.points[0].accounts[0].circuit_broken  # no future data leakage
    assert base.points[2].accounts[0].floating_pnl == "-3000.00"
    assert base.points[2].accounts[0].loss_percent == "3.00"
    assert base.points[2].accounts[0].circuit_broken
    assert not other.points[2].accounts[0].circuit_broken
    assert base.points[3].order.reason == "CIRCUIT_BROKEN"
    assert other.points[3].order.status == "FILLED"
    assert base.points[5].order.reason == "CIRCUIT_BROKEN"  # rebound remains blocked
    assert other.points[5].order.reason == "PROJECTED_EXPOSURE_LIMIT"
    assert base.points[7].accounts[0].settled_balance == "100390.00"
    assert other.points[7].accounts[0].settled_balance == "101488.00"
    assert not base.points[8].accounts[0].circuit_broken  # next day resets
    assert base.points[8].accounts[0].equity is None
    assert base.points[10].order.status == "FILLED"  # close yesterday
    assert base.points[-1].accounts[0].settled_balance == "99888.00"
    assert other.points[-1].accounts[0].settled_balance == "100786.00"
    assert base.report_count == other.report_count == 2
    assert base.points[-1].accounts[1:] == other.points[-1].accounts[1:]
    assert [e.frame_sequence for e in base.events if e.kind == "CIRCUIT_BREAK"] == [2]
    assert compare(ReplayRequest(dataset=dataset)).baseline == base


def test_replay_keeps_market_gate_and_idempotent_inputs() -> None:
    data = sample().model_dump()
    data["steps"] = data["steps"][:2]
    data["steps"][1]["market_ready"] = False
    data["steps"].append(data["steps"][1].copy())
    data["steps"].append(data["steps"][0].copy())
    result = compare(ReplayRequest(dataset=ReplayDataset.model_validate(data)))
    assert result.baseline.points[1].order.reason == "MARKET_UNAVAILABLE"
    assert result.baseline.points[2].order.duplicate
    assert not result.baseline.events


@pytest.mark.parametrize("mutation", ["gap", "price", "unknown_limit", "day"])
def test_invalid_history_and_rule_references_are_rejected(mutation: str) -> None:
    data = sample().model_dump()
    if mutation == "gap":
        data["steps"][0]["sequence"] = 2
    elif mutation == "price":
        data["steps"][0]["prices"]["SHFE.rb2610"] = "NaN"
    elif mutation == "day":
        data["steps"][0]["trading_day"] = "2026-09-10"
    else:
        data["policy"]["product_limits"][0]["account_id"] = "unknown"
    with pytest.raises(AccountingError):
        compare(ReplayRequest(dataset=ReplayDataset.model_validate(data)))


@pytest.mark.asyncio
async def test_export_replay_matches_live_ledger_and_survives_restart(tmp_path: Path) -> None:
    dataset = sample()
    settings = Settings(
        ledger=dataset.seed.ledger().model_copy(update={"database": tmp_path / "live.sqlite3"}),
        trading=dataset.policy,
        settlement=SettlementSettings(
            days=tuple(dataset.settlement_days), report_dir=tmp_path / "reports", auto_settle=False
        ),
    )
    exported = None
    for restart in (False, True):
        app = create_app(settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
            ) as client,
        ):
            service = app.state.ledger
            if not restart:
                for command in dataset.commands():
                    if isinstance(command, PriceFrame):
                        await service.publish_frame(command)
                    elif isinstance(command, OrderCommand):
                        await service.submit_order(command.order)
                    elif isinstance(command, DayCommand):
                        await service.day_command(command)
            before = state_digest(service.trading_state())
            response = await client.get("/api/replay/current")
            assert response.status_code == 200, response.text
            current = response.json()
            assert "database" not in current["seed"]
            if restart:
                assert current == exported
            exported = current
            assert (
                await client.post("/api/replay/run", json={"dataset": current})
            ).status_code == 401
            token = (await client.get("/api/session")).json()["token"]
            response = await client.post(
                "/api/replay/run",
                json={"dataset": current},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["baseline"]["matches_recording"] is True
            assert result["baseline"]["state_digest"] == before
            assert result["baseline"] == result["candidate"]
            assert state_digest(service.trading_state()) == before
            metrics = (await client.get("/api/workspace")).json()["risk_comparison"]
            assert metrics[0]["loss_limit"] == "3000.0000"
            assert metrics[0]["breaker_percent"] == "3.00"


@pytest.mark.asyncio
async def test_computation_does_not_hold_scene_lock(tmp_path: Path, monkeypatch) -> None:
    from cta_risk.api import replay as module
    from cta_risk.config.loader import load_settings
    from cta_risk.demonstration import DemoController
    from cta_risk.replay.models import ReplayRequest

    settings = load_settings(Path(__file__).resolve().parents[3] / "configs/default.yaml")
    app = create_app(
        Settings(
            ledger=sample().seed.ledger().model_copy(update={"database": tmp_path / "db"}),
            trading=sample().policy,
        )
    )
    entered, release = threading.Event(), threading.Event()
    original = module.compare

    def delayed(body: ReplayRequest):
        entered.set()
        assert release.wait(5)
        return original(body)

    monkeypatch.setattr(module, "compare", delayed)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8004"
        ) as client,
    ):
        controller = DemoController(app, settings)
        controller.active = controller.new_run("manual")
        app.state.demo = controller
        token = (await client.get("/api/session")).json()["token"]
        headers = {
            "Authorization": f"Bearer {token}",
            "X-CTA-Run-Id": controller.active.settings.ledger.run_id,
        }
        task = asyncio.create_task(
            client.post(
                "/api/replay/run",
                headers=headers,
                json={"dataset": sample().model_dump(mode="json")},
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert not controller.lock.locked()
            response = await asyncio.wait_for(client.get("/api/workspace"), 1)
            assert response.status_code == 200
            assert (
                await client.post(
                    "/api/replay/run",
                    headers=headers,
                    json={"dataset": sample().model_dump(mode="json")},
                )
            ).status_code == 429
        finally:
            release.set()
        assert (await task).status_code == 200
        app.state.demo = None


def test_duplicate_loss_frame_does_not_repeat_signals_or_change_final_state() -> None:
    dataset = sample()
    expected = compare(ReplayRequest(dataset=dataset)).baseline
    raw = dataset.model_dump()
    raw["steps"].insert(3, raw["steps"][2].copy())
    repeated = compare(ReplayRequest(dataset=ReplayDataset.model_validate(raw))).baseline
    assert repeated.state_digest == expected.state_digest
    assert len(repeated.events) == len(expected.events)
    assert sum(event.kind == "CIRCUIT_BREAK" for event in repeated.events) == 1
