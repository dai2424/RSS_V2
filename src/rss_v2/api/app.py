"""FastAPI 应用装配。"""

from __future__ import annotations

import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from rss_v2.api.routes import (
    categories_router,
    collection_router,
    messages_router,
    providers_router,
    sources_router,
    tasks_router,
)
from rss_v2.bootstrap import build_container
from rss_v2.domain import DomainError
from rss_v2.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建 FastAPI 应用。"""

    app = FastAPI(title="RSS v2", version="0.1.0")
    app.state.container = build_container(settings)
    app.include_router(categories_router)
    app.include_router(sources_router)
    app.include_router(collection_router)
    app.include_router(messages_router)
    app.include_router(tasks_router)
    app.include_router(providers_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        status_code = (
            404 if exc.code.endswith("_not_found") else 409 if exc.code.endswith("_exists") else 400
        )
        return JSONResponse(
            status_code=status_code, content={"code": exc.code, "message": exc.message}
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity_error_handler(_: Request, exc: sqlite3.IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"code": "integrity_error", "message": str(exc)}
        )

    return app
