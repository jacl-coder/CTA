# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent
package = root / "backend" / "src" / "cta_risk"
static = package / "static"
migrations = package / "storage" / "migrations"
if not (static / "index.html").is_file():
    raise SystemExit("Missing frontend build: run make build first")
if not list(migrations.glob("*.sql")):
    raise SystemExit("Missing database migrations")

analysis = Analysis(
    [str(root / "packaging" / "entrypoint.py")],
    pathex=[str(root / "backend" / "src")],
    datas=[(str(static), "cta_risk/static"), (str(migrations), "cta_risk/storage/migrations"),
           (str(package / "replay/sample.json"), "cta_risk/replay")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="cta-risk", console=True,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas, name="cta-risk",
)
