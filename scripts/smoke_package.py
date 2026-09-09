"""独立目录、无 Python/Node 搜索路径，验证发布包的账本初始化和进程恢复。"""

import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    executable = root / "dist" / "cta-risk" / "cta-risk"
    if not executable.is_file():
        raise SystemExit("发布程序不存在，请先运行 make package")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="cta-package-check-") as folder:
        directory = Path(folder)
        config = directory / "demo.yaml"
        raw = yaml.safe_load((root / "configs/demo.yaml").read_text(encoding="utf-8"))
        raw["server"] = {"host": "127.0.0.1", "port": port}
        raw["ledger"]["database"] = "ledger.sqlite3"
        config.write_text(yaml.safe_dump(raw), encoding="utf-8")
        environment = {"PATH": str(directory / "no-executables"), "LANG": "C.UTF-8"}
        if "SYSTEMROOT" in os.environ:
            environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        command = [str(executable), "serve", "--config", str(config)]
        baseline = None
        for run in (1, 2):
            with (directory / f"server-{run}.log").open("w+") as log:
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read())
                        try:
                            with urllib.request.urlopen(
                                base + "/api/health/live", timeout=0.5
                            ) as res:
                                if res.status == 200:
                                    break
                        except urllib.error.URLError:
                            time.sleep(0.1)
                    else:
                        raise RuntimeError("发布程序启动超时")
                    snapshots = {}
                    for route, expected in (
                        ("/", 200),
                        ("/api/system", 200),
                        ("/api/health/ready", 503),
                        ("/api/missing", 404),
                        ("/api/accounts", 200),
                        ("/api/positions", 200),
                        ("/api/positions/summary", 200),
                        ("/api/trades", 200),
                    ):
                        try:
                            response = urllib.request.urlopen(base + route, timeout=2)
                        except urllib.error.HTTPError as error:
                            response = error
                        with response:
                            if response.status != expected:
                                raise RuntimeError(f"{route}: {response.status} != {expected}")
                            if route.startswith("/api/") and expected == 200:
                                snapshots[route] = json.load(response)
                        print(f"run {run}: {route}: {expected}")
                    status = snapshots["/api/system"]
                    if status["trading_available"] or not status["ledger_available"]:
                        raise RuntimeError("账本应可查询，但尚不能交易")
                    accounts = snapshots["/api/accounts"]
                    if len(accounts) != 3 or accounts[0]["fees"] != "8.00":
                        raise RuntimeError("账户初始化结果错误")
                    if len(snapshots["/api/trades"]) != 4:
                        raise RuntimeError("初始化成交缺失或重复")
                    if baseline is not None and snapshots != baseline:
                        raise RuntimeError("进程重启后查询结果发生变化")
                    baseline = snapshots
                finally:
                    if process.poll() is None:
                        # 首轮强制退出，验证已提交账本与文件锁可在新进程恢复。
                        if run == 1:
                            process.kill()
                        else:
                            process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            with sqlite3.connect(directory / "ledger.sqlite3") as connection:
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("数据库完整性检查失败")
                if connection.execute("PRAGMA foreign_key_check").fetchall():
                    raise RuntimeError("数据库存在孤立记录")
    print("发布包页面、迁移资源、初始化与强制退出后的重启恢复检查通过。")
    print("完整风控业务和跨机器兼容性仍需另行验收。")


if __name__ == "__main__":
    main()
