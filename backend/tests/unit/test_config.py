from pathlib import Path

import pytest

from cta_risk.config.loader import ConfigurationError, load_settings


@pytest.mark.parametrize(
    "content",
    [
        "server:\n  port: 8000\n  port: 8001\n",
        "server:\n  port: true\n",
        "server:\n  port: 70000\n",
        "server:\n  ports: 8000\n",
        "server: !!python/object:builtins.object {}",
        "[]",
    ],
)
def test_rejects_ambiguous_or_invalid_configuration(tmp_path: Path, content: str) -> None:
    config = tmp_path / "demo.yaml"
    config.write_text(content)
    with pytest.raises(ConfigurationError):
        load_settings(config)


def test_paths_are_relative_to_configuration(tmp_path: Path) -> None:
    config = tmp_path / "demo.yaml"
    config.write_text("frontend_dir: ../frontend/dist\n")
    assert load_settings(config).frontend_dir == (tmp_path.parent / "frontend/dist").resolve()
