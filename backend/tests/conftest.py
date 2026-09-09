from pathlib import Path

import pytest
import yaml

from cta_risk.config.loader import load_settings
from cta_risk.config.models import Settings


@pytest.fixture
def demo_config(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "configs/demo.yaml").read_text())
    raw["ledger"]["database"] = "ledger.sqlite3"
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture
def demo_settings(demo_config: Path) -> Settings:
    return load_settings(demo_config)
