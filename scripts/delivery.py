"""汇集源码、配置、文档和可执行目录，并验证实际解压后的交付包。"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = "cta-risk-delivery"
ARCHIVE = ROOT / "dist" / f"{BUNDLE}-{platform.system().lower()}-{platform.machine()}.tar.gz"
SOURCE_DIRS = {"backend", "frontend", "configs", "docs", "packaging", "scripts", "testdata"}
SOURCE_FILES = {"README.md", "Makefile", ".editorconfig", ".gitignore"}
IGNORED = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    "playwright-report",
    "test-results",
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_paths(root: Path) -> list[Path]:
    """Git 工作区纳入未忽略的新文件；解压源码没有 .git 时仍支持重新打包。"""
    if (root / ".git").exists():
        output = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        )
        candidates = [Path(os.fsdecode(name)) for name in output.split(b"\0") if name]
    else:
        candidates = [Path(name) for name in SOURCE_FILES if (root / name).is_file()]
        for name in sorted(SOURCE_DIRS):
            for directory, directories, files in os.walk(root / name):
                directories[:] = [part for part in directories if part not in IGNORED]
                candidates.extend((Path(directory) / file).relative_to(root) for file in files)
    result = []
    for path in sorted(set(candidates)):
        if path.parts[0] not in SOURCE_DIRS and str(path) not in SOURCE_FILES:
            continue
        if any(part in IGNORED or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.is_relative_to("backend/src/cta_risk/static"):
            continue
        if path.name.startswith(".env") or path.suffix in {".log", ".pyc", ".tsbuildinfo"}:
            continue
        source = root / path
        if source.is_symlink():
            raise ValueError(f"源码不应包含符号链接：{path}")
        if source.is_file():
            result.append(path)
    return result


def inventory(root: Path) -> dict[str, dict[str, object]]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"交付包不支持符号链接：{path.relative_to(root)}")
        if path.is_file() and path != root / "MANIFEST.json":
            result[path.relative_to(root).as_posix()] = {
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "executable": bool(path.stat().st_mode & 0o111),
            }
    return result


def check_manifest(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "MANIFEST.json").read_text())
    if manifest.get("format") != 1 or manifest.get("files") != inventory(root):
        raise ValueError("交付包文件缺失、被修改、权限变化或存在未登记文件")
    return manifest


def assemble(root: Path = ROOT, archive: Path = ARCHIVE) -> None:
    executable = root / "dist/cta-risk/cta-risk"
    if not executable.is_file():
        raise ValueError("请先通过 make package 构建可执行目录")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cta-delivery-build-", dir=archive.parent) as folder:
        destination = Path(folder) / BUNDLE
        destination.mkdir()
        for relative in source_paths(root):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, target)
        shutil.copytree(root / "dist/cta-risk", destination / "dist/cta-risk", symlinks=True)
        commit = None
        dirty = None
        if (root / ".git").exists():
            commit = (
                subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
            )
            dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root))
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "base_commit": commit,
            "working_tree_dirty": dirty,
            "platform": {
                "system": platform.system(),
                "machine": platform.machine(),
                "libc": platform.libc_ver(),
            },
            "verification": "运行 make verify 后查看包外的验证记录；本清单不表示验收通过",
            "files": inventory(destination),
        }
        (destination / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        )
        staged = Path(folder) / archive.name
        with tarfile.open(staged, "w:gz") as package:
            package.add(destination, arcname=BUNDLE)
        staged.replace(archive)
    archive.with_name(archive.name + ".sha256").write_text(f"{sha256(archive)}  {archive.name}\n")
    print(f"完整交付包：{archive}")


def unpack(archive: Path, directory: Path) -> Path:
    with tarfile.open(archive, "r:gz") as package:
        members = package.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise ValueError("交付包包含重复路径")
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or path.parts[0] != BUNDLE
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError(f"交付包路径或文件类型非法：{member.name}")
        package.extractall(directory, filter="data")
    root = directory / BUNDLE
    check_manifest(root)
    return root


def verify(archive: Path = ARCHIVE) -> None:
    digest = sha256(archive)
    evidence = archive.parent / "delivery-verification" / digest[:16]
    evidence.mkdir(parents=True, exist_ok=True)
    result = {
        "archive": archive.name,
        "archive_sha256": digest,
        "started_at": datetime.now(UTC).isoformat(),
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "libc": platform.libc_ver(),
        },
        "passed": False,
        "checks": [],
    }
    checks = result["checks"]
    try:
        with tempfile.TemporaryDirectory(prefix="cta-delivery-unpack-") as folder:
            root = unpack(archive, Path(folder))
            print("交付包解压与逐文件 SHA-256 / 可执行权限核对通过", flush=True)
            for name, arguments in (
                ("smoke_package", []),
                ("demo_risk", ["--packaged"]),
                ("demo_market", ["--packaged"]),
                ("demo_settlement", ["--packaged"]),
                ("demo_live", ["--packaged"]),
            ):
                print(f"验证解压包：{name}", flush=True)
                command = [sys.executable, str(root / "scripts" / f"{name}.py"), *arguments]
                with (evidence / f"{name}.log").open("w") as log:
                    process = subprocess.Popen(
                        command,
                        cwd=root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        code = process.wait(timeout=180)
                    finally:
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGTERM)
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
                checks.append({"name": name, "exit_code": code, "log": f"{name}.log"})
                if code:
                    raise RuntimeError(f"{name} 失败，查看 {evidence / (name + '.log')}")
        result["passed"] = True
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        result["finished_at"] = datetime.now(UTC).isoformat()
        (evidence / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
    print(f"实际解压包验收通过，记录：{evidence / 'result.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("assemble", "verify"))
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    if args.command == "assemble":
        assemble(archive=args.archive.resolve())
    else:
        verify(args.archive.resolve())


if __name__ == "__main__":
    main()
