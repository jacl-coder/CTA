"""真实进程验收：同一地址切换三种场景、保留旧账本并恢复当前运行。"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml
from demo_live import ready
from demo_market import free_ports, source_children, wait_sources_exit
from demo_risk import server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaged", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cta-workspace-demo-") as folder:
        directory = Path(folder)
        ports = free_ports()
        base = f"http://127.0.0.1:{ports[0]}"
        raw = yaml.safe_load((root / "configs/default.yaml").read_text())
        raw["server"]["port"] = ports[0]
        raw["demo"]["directory"] = "runs"
        raw["ledger"]["database"] = "unused.sqlite3"
        raw["demo"]["fault_market"]["interval_ms"] = 100
        for market in (raw["market"], raw["demo"]["fault_market"]):
            for source, port in zip(market["sources"], ports[1:], strict=True):
                source["port"] = port
        config = directory / "default.yaml"
        config.write_text(yaml.safe_dump(raw))
        prefix = (
            [str(root / "dist/cta-risk/cta-risk")]
            if args.packaged
            else [sys.executable, "-m", "cta_risk"]
        )
        command = [*prefix, "serve", "--config", str(config)]
        environment = (
            {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
            if args.packaged
            else dict(os.environ)
        )
        with httpx.Client(base_url=base, timeout=20) as client:

            def read(path):
                response = client.get(path)
                response.raise_for_status()
                return response.json()

            def post(path, body, run_id, status=200):
                token = read("/api/session")["token"]
                response = client.post(
                    path,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "X-CTA-Run-Id": run_id,
                    },
                )
                assert response.status_code == status, response.text
                return response.json()

            with server(command, directory, base, environment, "switching") as process:
                normal = ready(base)
                assert normal["demo"]["mode"] == "continuous"
                old_children = source_children(process.pid)
                assert len(old_children) == 2
                post("/api/demo/switch", {"mode": "manual"}, normal["run_id"])
                wait_sources_exit(old_children)
                assert source_children(process.pid) == []
                manual = read("/api/workspace")
                run_id = manual["run_id"]
                assert manual["demo"]["mode"] == "manual"
                assert not manual["system"]["trading_available"]
                prices = manual["demo"]["initial_prices"]
                day = manual["accounts"][0]["trading_day"]
                post(
                    "/api/simulation/frames",
                    {"sequence": 1, "trading_day": day, "prices": prices},
                    run_id,
                )
                order = {
                    "request_id": "manual-open",
                    "account_id": "A",
                    "instrument_id": "SHFE.rb2610",
                    "trading_day": day,
                    "side": "LONG",
                    "offset": "OPEN",
                    "quantity": 1,
                }
                assert post("/api/orders", order, run_id)["status"] == "FILLED"
                prices["SHFE.rb2610"] = "3400"
                post(
                    "/api/simulation/frames",
                    {"sequence": 2, "trading_day": day, "prices": prices},
                    run_id,
                )
                assert read("/api/risk")[0]["circuit_broken"]
                prices["SHFE.rb2610"] = "3500"
                post(
                    "/api/simulation/frames",
                    {"sequence": 3, "trading_day": day, "prices": prices},
                    run_id,
                )
                assert read("/api/risk")[0]["circuit_broken"]
                post("/api/settlement/close", {"trading_day": day}, run_id)
                settlement_prices = read("/api/workspace")["settlement_days"][0]["prices"]
                post(
                    "/api/settlement/settle",
                    {"trading_day": day, "prices": settlement_prices},
                    run_id,
                )
                report = read(f"/api/reports/{day}")
                assert report["accounts"][0]["settlement"]["closing_balance"] == "100390.00"
                print(
                    "同一进程：持续行情 → 手工交易 → 3000 元熔断 → 反弹保持 → 清算 100390 元",
                    flush=True,
                )
                history = read("/api/replay/current")
                before = read("/api/workspace")["revision"]
                replay = post("/api/replay/run", {"dataset": history}, run_id)
                assert replay["baseline"]["matches_recording"]
                assert replay["baseline"] == replay["candidate"]
                assert read("/api/workspace")["revision"] == before
                example = read("/api/replay/example")
                candidate = {**example["policy"], "loss_ratio": "0.05"}
                compared = post(
                    "/api/replay/run",
                    {
                        "dataset": example,
                        "candidate_policy": candidate,
                    },
                    run_id,
                )
                assert compared["baseline"]["points"][3]["order"]["reason"] == "CIRCUIT_BROKEN"
                assert compared["candidate"]["points"][3]["order"]["status"] == "FILLED"
                assert (
                    compared["baseline"]["points"][7]["accounts"][0]["settled_balance"]
                    == "100390.00"
                )
                assert (
                    compared["candidate"]["points"][7]["accounts"][0]["settled_balance"]
                    == "101488.00"
                )
                assert read("/api/workspace")["revision"] == before
                print("历史导出与完整状态重算一致、3%/5% 规则对照和在线账本隔离通过", flush=True)
                post("/api/demo/switch", {"mode": "fault"}, run_id)
                post("/api/settlement/close", {"trading_day": day}, run_id, status=409)
                fault = read("/api/workspace")
                fault_run = fault["run_id"]
                assert fault["demo"]["mode"] == "fault"
                assert len(read("/api/trades")) == 4
                assert len(source_children(process.pid)) == 2
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    fault = read("/api/workspace")
                    if len(fault["settlement"]["settled_days"]) == 2:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("故障场景未完成两日清算")
                breaks = [
                    event for event in read("/api/risk-events") if event["kind"] == "CIRCUIT_BREAK"
                ]
                assert [(event["account_id"], event["frame_sequence"]) for event in breaks] == [
                    ("A", 10),
                    ("B", 32),
                ]
                assert all(event["recovered"] for event in breaks)
                assert len(list((directory / "runs").glob("*/ledger.sqlite3"))) == 3
                children = source_children(process.pid)
            wait_sources_exit(children)
            with server(command, directory, base, environment, "restored") as process:
                restored = read("/api/workspace")
                assert restored["run_id"] == fault_run
                assert restored["demo"]["mode"] == "fault"
                assert len(read("/api/reports")) == 2
                post("/api/demo/switch", {"mode": "fault"}, fault_run)
                assert read("/api/workspace")["run_id"] != fault_run
                current = read("/api/workspace")
                children = source_children(process.pid)
                post("/api/demo/switch", {"mode": "continuous"}, current["run_id"])
                wait_sources_exit(children)
                assert ready(base)["demo"]["mode"] == "continuous"
                children = source_children(process.pid)
            wait_sources_exit(children)
        print(
            "双源补数与两日清算、旧请求拒绝、历史隔离、重启恢复、重新开始和返回正常运行均通过。",
            flush=True,
        )


if __name__ == "__main__":
    main()
