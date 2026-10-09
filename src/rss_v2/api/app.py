"""FastAPI 应用装配。"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from rss_v2.api.routes import (
    categories_router,
    collection_router,
    keywords_router,
    messages_router,
    prompts_router,
    providers_router,
    sources_router,
    task_settings_router,
    tasks_router,
)
from rss_v2.api.static import FrontendFiles
from rss_v2.bootstrap import build_container
from rss_v2.domain import ConflictError, DomainError
from rss_v2.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 FastAPI 应用。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.container = build_container(settings)
        yield

    app = FastAPI(title="RSS v2", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(categories_router)
    app.include_router(sources_router)
    app.include_router(collection_router)
    app.include_router(messages_router)
    app.include_router(keywords_router)
    app.include_router(tasks_router)
    app.include_router(providers_router)
    app.include_router(prompts_router)
    app.include_router(task_settings_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        status_code = (
            404
            if exc.code.endswith("_not_found")
            else 409
            if isinstance(exc, ConflictError) or exc.code.endswith("_exists")
            else 400
        )
        return JSONResponse(
            status_code=status_code, content={"code": exc.code, "message": exc.message}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "请求字段校验失败",
                "fields": [
                    {
                        "field": ".".join(str(value) for value in error["loc"][1:]),
                        "message": error["msg"],
                    }
                    for error in exc.errors()
                ],
            },
        )

    @app.middleware("http")
    async def local_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse(
                status_code=403, content={"code": "local_only", "message": "当前版本仅支持本机访问"}
            )
        allowed_hosts = {"127.0.0.1", "::1", "localhost", "testserver"}
        origin = request.headers.get("origin")
        if request.url.hostname not in allowed_hosts or (
            origin and urlsplit(origin).hostname not in allowed_hosts
        ):
            return JSONResponse(
                status_code=403, content={"code": "local_only", "message": "当前版本仅支持本机来源"}
            )
        return await call_next(request)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("api_error class=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务发生内部错误，请查看运行日志"},
        )

    frontend_dist = (settings or Settings()).frontend_dist
    if frontend_dist.exists():
        app.mount("/", FrontendFiles(directory=frontend_dist, html=True), name="frontend")

    return app
