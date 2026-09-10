"""持续双源工作台的实际进程验收：自动行情、开平仓、跨日及重启幂等。"""

import argparse
import json
import os
import sys
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import yaml
from demo_market import free_ports, source_children, wait_sources_exit
from demo_risk import request, server


def ready(base: str, minimum: int = 1) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        value = request(base, "/api/workspace")
        if value["system"]["trading_available"] and value["sources"]["applied_sequence"] >= minimum:
            return value
        time.sleep(0.05)
    raise AssertionError(f"持续工作台未就绪：{value['system']}, {value.get('sources')}")


def submit(base: str, offset: str, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for attempt in range(10):
        snapshot = ready(base)
        order = {
            "request_id": f"{label}-{attempt}",
            "account_id": "B",
            "instrument_id": "DCE.i2701",
            "trading_day": snapshot["settlement"]["trading_day"],
            "side": "SHORT",
            "offset": offset,
            "quantity": 1,
        }
        result = request(base, "/api/orders", order, request(base, "/api/session")["token"])
        if result["status"] == "FILLED":
            return order, result
        # A quote/day may advance between the read and write. Retry only proven rejections.
        assert result["reason"] in {"MARKET_UNAVAILABLE", "TRADING_DAY_CLOSED"}, result
    raise AssertionError("持续行情期间未能成交")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cta-live-demo-") as folder:
        directory = Path(folder)
        ports = free_ports()
        base = f"http://127.0.0.1:{ports[0]}"
        raw = yaml.safe_load((root / "configs/live-demo.yaml").read_text())
        raw["server"]["port"] = ports[0]
        raw["ledger"]["database"] = "ledger.sqlite3"
        raw["settlement"]["report_dir"] = "reports"
        # Keep the default generator, risk rules and one-second quotes; shorten each day
        # to eight seconds so acceptance covers several rollovers without waiting an hour.
        frames_per_day = 8
        raw["market"]["frame_count"] = frames_per_day
        raw["market"]["sessions"][0]["close_sequence"] = frames_per_day
        raw["settlement"]["days"][0]["close_sequence"] = frames_per_day
        for source, port in zip(raw["market"]["sources"], ports[1:], strict=True):
            source["port"] = port
        config = directory / "demo.yaml"
        config.write_text(yaml.safe_dump(raw))
        if args.packaged:
            command = [str(root / "dist/cta-risk/cta-risk")]
            environment = {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
        else:
            command = [sys.executable, "-m", "cta_risk"]
            environment = dict(os.environ)
        command += ["serve", "--config", str(config)]
        try:
            with ExitStack() as stack:
                process = stack.enter_context(
                    server(command, directory, base, environment, "first")
                )
                first = ready(base)
                assert first["capabilities"]["multi_source"]
                assert not first["capabilities"]["manual_market"]
                assert len(source_children(process.pid)) == 2
                order, opened = submit(base, "OPEN", "live-open")
                # Source identities are independent actual HTTP endpoints.
                for source, port in zip(raw["market"]["sources"], ports[1:], strict=True):
                    head = request(f"http://127.0.0.1:{port}", "/head")
                    assert head["source_id"] == source["source_id"] and not head["complete"]
                first_day = first["settlement"]["trading_day"]
                second = ready(base, frames_per_day + 1)
                assert second["settlement"]["trading_day"] != first_day
                _, closed = submit(base, "CLOSE_YESTERDAY", "live-close")
                report = request(base, f"/api/reports/{first_day}")
                assert report["close_sequence"] == frames_per_day
                print("双源自动就绪、模拟开仓和跨日平昨成交，首日日报已冻结", flush=True)
                children = source_children(process.pid)
                assert len(children) == 2
                process.kill()
                process.wait(timeout=5)
                wait_sources_exit(children)
                stack.enter_context(server(command, directory, base, environment, "restart"))
                restored = ready(base, second["sources"]["applied_sequence"] + 1)
                assert restored["sources"]["epoch_ms"] == first["sources"]["epoch_ms"]
                retry = request(base, "/api/orders", order, request(base, "/api/session")["token"])
                assert retry["duplicate"] and retry["fill_id"] == opened["fill_id"]
                assert request(base, f"/api/reports/{first_day}") == report
                final = ready(base, frames_per_day * 2 + 1)
                assert len(final["settlement"]["settled_days"]) >= 2
                assert not final["sources"]["completed"]
                assert final["sources"]["applied_sequence"] > frames_per_day * 2
                assert all(source["connected"] for source in final["sources"]["sources"])
                token = request(base, "/api/session")["token"]
                files = request(base, f"/api/reports/{first_day}/export", {}, token)
                assert json.loads((directory / "reports" / files["json"]).read_text()) == report
                print(
                    f"重启补数、原订单幂等和日报不变；已进入第三模拟日，"
                    f"持续应用至第 {final['sources']['applied_sequence']} 帧，"
                    f"开仓/平昨均为 {opened['status']}/{closed['status']}",
                    flush=True,
                )
        except Exception:
            for log in sorted(directory.rglob("*.log")):
                print(f"{log}:\n{log.read_text(errors='replace')[-4000:]}", file=sys.stderr)
            raise
    print("持续双源工作台实际进程验收通过。")


if __name__ == "__main__":
    main()
