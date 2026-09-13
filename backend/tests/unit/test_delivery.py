"""交付物内容与校验：防止空包、漏文件和误把开发机数据交给评审。"""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "delivery", Path(__file__).resolve().parents[3] / "scripts/delivery.py"
)
assert spec is not None and spec.loader is not None
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)


def write(root: Path, relative: str, content: str = "demo") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_assemble_and_unpack_without_git_preserves_sources_and_executable(tmp_path: Path) -> None:
    root = tmp_path / "source"
    write(root, "README.md", "使用说明")
    write(root, "backend/src/demo.py", "print('hello')")
    write(root, "configs/presentation.yaml", "server: {}")
    write(root, "frontend/node_modules/secret.txt")
    write(root, "backend/.venv/secret.txt")
    write(root, "backend/src/cta_risk/static/old.js")
    write(root, "configs/.env.local")
    write(root, "var/ledger.sqlite3")
    write(root, "frontend/dist/old.js")
    executable = write(root, "dist/cta-risk/cta-risk", "#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    archive = tmp_path / "delivery.tar.gz"
    delivery.assemble(root, archive)
    extracted = delivery.unpack(archive, tmp_path / "extracted")
    manifest = delivery.check_manifest(extracted)
    assert set(manifest["files"]) == {
        "README.md",
        "backend/src/demo.py",
        "configs/presentation.yaml",
        "dist/cta-risk/cta-risk",
    }
    assert manifest["base_commit"] is None
    assert manifest["files"]["dist/cta-risk/cta-risk"]["executable"]
    assert (extracted / "README.md").read_text() == "使用说明"
    assert archive.with_name(archive.name + ".sha256").read_text().split()[0] == delivery.sha256(
        archive
    )


@pytest.mark.parametrize("change", ["modified", "missing", "extra", "permission"])
def test_manifest_detects_changes_before_execution(tmp_path: Path, change: str) -> None:
    path = write(tmp_path, "dist/cta-risk/cta-risk", "runtime")
    path.chmod(0o755)
    write(
        tmp_path, "MANIFEST.json", json.dumps({"format": 1, "files": delivery.inventory(tmp_path)})
    )
    if change == "modified":
        path.write_text("wrong runtime")
    elif change == "missing":
        path.unlink()
    elif change == "extra":
        write(tmp_path, "unexpected.txt")
    else:
        path.chmod(0o644)
    with pytest.raises(ValueError, match="交付包文件"):
        delivery.check_manifest(tmp_path)


@pytest.mark.parametrize("name", ["../outside", "/tmp/outside", "other/file"])
def test_unpack_rejects_paths_outside_bundle(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as package:
        item = tarfile.TarInfo(name)
        item.size = 4
        package.addfile(item, io.BytesIO(b"data"))
    with pytest.raises(ValueError, match="路径或文件类型非法"):
        delivery.unpack(archive, tmp_path / "extract")
    assert not (tmp_path / "outside").exists()
