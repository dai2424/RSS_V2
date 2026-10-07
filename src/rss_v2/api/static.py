"""构建产物托管与前端路由刷新回退。"""

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class FrontendFiles(StaticFiles):
    """仅已知前端页面回退 index，缺失资源和 API 保持 404。"""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # Starlette 在 Windows 用本地路径分隔符规范化 URL 路径。
            route = path.replace("\\", "/").lstrip("/").split("/")[0]
            if exc.status_code != 404 or route not in {
                "sources",
                "messages",
                "tasks",
                "settings",
            }:
                raise
            return await super().get_response("index.html", scope)
