"""回环地址演示写入凭据；每次启动生成，不写入配置或日志。"""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def check_origin(request: Request) -> None:
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "会话与写入接口仅支持回环地址 Host")
    origin = request.headers.get("origin")
    allowed = {str(request.base_url).rstrip("/"), "http://127.0.0.1:5173", "http://localhost:5173"}
    if origin is not None and origin not in allowed:
        raise HTTPException(403, "写入与会话接口仅允许本地同源请求")


def require_session(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    check_origin(request)
    token = request.app.state.write_token
    if (
        credentials is None
        or token is None
        or not secrets.compare_digest(credentials.credentials, token)
    ):
        raise HTTPException(401, "需要本次运行的 Bearer 凭据")
