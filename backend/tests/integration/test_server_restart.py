"""真实子进程验证重启、账本恢复、行情退出和外部端口保护。"""

import json
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def free_ports(count: int) -> list[int]:
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for item in sockets:
            item.bind(("127.0.0.1", 0))
        return [item.getsockname()[1] for item in sockets]


def config_file(directory: Path, ports: list[int], database: str, managed: bool = False) -> Path:
    template = "live-demo.yaml" if managed else "presentation.yaml"
    raw = yaml.safe_load((ROOT / "testdata/configs" / template).read_text())
    raw["server"]["port"] = ports[0]
    raw["ledger"]["database"] = str(directory / database)
    raw["settlement"]["report_dir"] = str(directory / "reports")
    if managed:
        for source, port in zip(raw["market"]["sources"], ports[1:], strict=True):
            source["port"] = port
    path = directory / f"{ports[0]}-{database}.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def start(config: Path, log, launcher: bool = False) -> subprocess.Popen:
    command = [str(ROOT / "backend/.venv/bin/python3")]
    command += ["scripts/run_server.py"] if launcher else ["-m", "cta_risk", "serve"]
    return subprocess.Popen(
        [*command, "--config", str(config)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT
    )


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def get(port: int, path: str):
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
        return json.load(response)


def ready(
    process: subprocess.Popen, port: int, previous: str | None = None, trading: bool = False
) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert process.poll() is None, "服务启动失败，查看测试目录 server.log"
        try:
            workspace = get(port, "/api/workspace")
            if workspace["instance_id"] != previous and (
                not trading or workspace["system"]["trading_available"]
            ):
                return
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.1)
    pytest.fail("服务未就绪")


@pytest.mark.parametrize("conflict", ["database", "port", "source"])
def test_restart_releases_resources_and_keeps_data(tmp_path: Path, conflict: str) -> None:
    ports = free_ports(6)
    managed = conflict == "source"
    original = config_file(tmp_path, ports[:3], "old.sqlite3", managed)
    independent = config_file(tmp_path, [ports[4]], "independent.sqlite3")
    with (tmp_path / "server.log").open("w") as log, ExitStack() as stack:
        old = start(original, log)
        stack.callback(stop, old)
        other = start(independent, log)
        stack.callback(stop, other)
        ready(old, ports[0], trading=managed)
        ready(other, ports[4])
        old_instance = get(ports[0], "/api/workspace")["instance_id"]
        before = get(ports[0], "/api/trades")
        inode = (tmp_path / "old.sqlite3").stat().st_ino
        child_ids = Path(f"/proc/{old.pid}/task/{old.pid}/children").read_text().split()
        if managed:
            assert len(child_ids) == 2
        new_port = ports[0] if conflict == "port" else ports[3]
        new_database = "new.sqlite3" if conflict == "port" else "old.sqlite3"
        # source 场景改用不同账本及主端口，仅通过行情端口识别冲突父进程。
        if conflict == "source":
            new_database = "source.sqlite3"
        replacement = config_file(tmp_path, [new_port, *ports[1:3]], new_database, managed)
        # 模拟旧配置被编辑：进程实际占用资源仍须能识别。
        if conflict == "database":
            original.write_text(replacement.read_text())
        new = start(replacement, log, launcher=True)
        stack.callback(stop, new)
        ready(new, new_port, previous=old_instance, trading=managed)
        # Uvicorn 正常清理后重新抛出原始 SIGTERM，退出码为 -15。
        assert old.wait(timeout=2) in (0, -signal.SIGTERM)
        assert other.poll() is None
        assert (tmp_path / "old.sqlite3").stat().st_ino == inode
        if conflict == "database":
            assert get(new_port, "/api/trades") == before
        for child in child_ids:
            assert not Path(f"/proc/{child}").exists()
        assert "停止本项目的冲突服务" in (tmp_path / "server.log").read_text()
        assert "Application shutdown complete." in (tmp_path / "server.log").read_text()


def test_external_listener_is_not_stopped(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        config = config_file(tmp_path, [port], "unused.sqlite3")
        result = subprocess.run(
            [sys.executable, "scripts/run_server.py", "--config", str(config)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1
        assert "其他程序占用" in result.stderr
        assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        assert not (tmp_path / "unused.sqlite3").exists()
