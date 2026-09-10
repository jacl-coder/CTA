"""将生产构建资源复制到后端包，开发服务不依赖此步骤。"""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "frontend" / "dist"
target = root / "backend" / "src" / "cta_risk" / "static"
if not (source / "index.html").is_file():
    raise SystemExit("前端尚未构建，请先运行 make build")
# This path contains generated frontend assets only; never remove runtime data.
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
print(f"前端资源已同步：{target}")
