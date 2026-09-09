"""从应用导出稳定的 API 契约，不启动网络服务。"""

import json
from pathlib import Path

from cta_risk.bootstrap import create_app

root = Path(__file__).resolve().parents[1]
target = root / "docs" / "openapi.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(target)
