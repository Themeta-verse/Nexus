from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .api import router
from .config import Settings
from .db import Database
from .rate_limit import SlidingWindowRateLimiter

logger = logging.getLogger("nexus")


class RequestGuardsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Callable, max_body_bytes: int):
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id[:128]
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Request body is too large",
                            "request_id": request.state.request_id,
                        },
                        headers={"X-Request-ID": request.state.request_id},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": "Invalid Content-Length",
                        "request_id": request.state.request_id,
                    },
                    headers={"X-Request-ID": request.state.request_id},
                )

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_environment()
    database = Database(runtime_settings.database_path)
    database.initialize()

    app = FastAPI(
        title=runtime_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if runtime_settings.environment != "production" else None,
        redoc_url="/redoc" if runtime_settings.environment != "production" else None,
    )
    app.state.settings = runtime_settings
    app.state.database = database
    app.state.rate_limiter = SlidingWindowRateLimiter(
        runtime_settings.login_rate_limit, runtime_settings.login_rate_window_seconds
    )
    app.add_middleware(RequestGuardsMiddleware, max_body_bytes=runtime_settings.max_body_bytes)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed", "request_id": request.state.request_id},
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled request failure request_id=%s", request.state.request_id)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "request_id": request.state.request_id},
            headers={"X-Request-ID": request.state.request_id},
        )

    return app


def run() -> None:
    uvicorn.run("nexus.main:create_app", factory=True, host="127.0.0.1", port=8000)


app = create_app()
