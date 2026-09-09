"""静态页面与 SPA 路由；缺失 API/资源不得回退成 200 HTML。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def mount_frontend(app: FastAPI, directory: Path) -> None:
    root = directory.resolve()
    if not (root / "index.html").is_file():
        raise ValueError(f"前端构建资源缺失：{root}/index.html")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404)
        if candidate.is_file():
            return FileResponse(candidate)
        if path.startswith("assets/") or Path(path).suffix:
            raise HTTPException(status_code=404)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
