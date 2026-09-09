from pathlib import Path

import pytest
import yaml

from cta_risk.bootstrap import create_app
from cta_risk.config.loader import ConfigurationError, load_settings


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_account",
        "duplicate_instrument",
        "duplicate_fill",
        "unknown_account",
        "unknown_instrument",
        "float_money",
        "invalid_day",
        "unquoted_day",
        "over_close",
        "missing_source",
        "memory_database",
        "unknown_field",
    ],
)
def test_invalid_seed_rejected_without_creating_database(demo_config: Path, mutation: str) -> None:
    raw = yaml.safe_load(demo_config.read_text())
    ledger = raw["ledger"]
    if mutation == "duplicate_account":
        ledger["accounts"].append(ledger["accounts"][0].copy())
    elif mutation == "duplicate_instrument":
        ledger["instruments"].append(ledger["instruments"][0].copy())
    elif mutation == "duplicate_fill":
        ledger["initial_fills"].append(ledger["initial_fills"][0].copy())
    elif mutation == "unknown_account":
        ledger["initial_fills"][0]["account_id"] = "missing"
    elif mutation == "unknown_instrument":
        ledger["initial_fills"][0]["instrument_id"] = "missing"
    elif mutation == "float_money":
        ledger["accounts"][0]["initial_capital"] = 100000.0
    elif mutation == "invalid_day":
        ledger["trading_day"] = "20260909"
    elif mutation == "unquoted_day":
        ledger["trading_day"] = yaml.safe_load("2026-09-09")
    elif mutation == "over_close":
        ledger["initial_fills"][1]["quantity"] = 4
    elif mutation == "missing_source":
        del ledger["initial_fills"][0]["source"]
    elif mutation == "memory_database":
        ledger["database"] = ":memory:"
    elif mutation == "unknown_field":
        ledger["accouts"] = []
    demo_config.write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigurationError):
        load_settings(demo_config)
    assert not (demo_config.parent / "ledger.sqlite3").exists()


def test_config_and_openapi_are_side_effect_free_and_infrastructure_is_not_business(
    demo_config: Path,
) -> None:
    settings = load_settings(demo_config)
    assert settings.ledger is not None
    assert settings.ledger.database == demo_config.parent / "ledger.sqlite3"
    fingerprint = settings.ledger.definition().fingerprint
    assert "/api/accounts" in create_app(settings).openapi()["paths"]
    assert not settings.ledger.database.exists()
    raw = yaml.safe_load(demo_config.read_text())
    raw["server"]["port"] = 8001
    raw["ledger"]["database"] = "other.sqlite3"
    raw["ledger"]["queue_capacity"] = 16
    demo_config.write_text(yaml.safe_dump(raw))
    changed = load_settings(demo_config)
    assert changed.ledger is not None
    assert changed.ledger.definition().fingerprint == fingerprint
    raw["ledger"]["accounts"][0]["initial_capital"] = "100001.00"
    demo_config.write_text(yaml.safe_dump(raw))
    changed = load_settings(demo_config)
    assert changed.ledger is not None
    assert changed.ledger.definition().fingerprint != fingerprint
