"""从固定演示模板创建一个新的本地账本，然后启动工作台。"""

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_server.py"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", action="store_true", help="创建双源中断恢复场景")
    args = parser.parse_args()
    template_name = "configs/settlement-demo.yaml" if args.sources else "configs/presentation.yaml"
    template = ROOT / template_name
    category = "source-runs" if args.sources else "presentation-runs"
    label = "sources" if args.sources else "presentation"
    session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "var" / category / session_id
    config_path = directory / "presentation.yaml"
    with template.open(encoding="utf-8") as stream:
        config: dict[str, Any] = yaml.safe_load(stream)

    config["ledger"]["database"] = "ledger.sqlite3"
    config["ledger"]["run_id"] = f"{label}-{session_id}"
    config["settlement"]["report_dir"] = "reports"
    directory.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"已创建新的演示数据：var/{category}/{session_id}", flush=True)
    os.execv(sys.executable, [sys.executable, str(RUNNER), "--config", str(config_path)])


if __name__ == "__main__":
    main()
