"""Application entrypoint — builds the FastAPI app.

Middleware order: correlation ID → secure headers → routers.
The app owns a shared async HTTP pool for all outbound engine/provider
traffic (connection reuse, bounded concurrency, explicit timeouts).
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import ai as ai_routes
from app.api.routes import examinations as exam_routes
from app.api.routes import health as health_routes
from app.api.routes import results as results_routes
from app.api.routes import students as student_routes
from app.api.routes import uploads as upload_routes
from app.config import BASE_DIR, Settings, get_settings
from app.db import init_db
from app.logging_setup import get_logger, get_request_id, log_event, set_request_id, setup_logging
from app.security import SECURE_HEADERS
from app.services.engine_client import EngineError
from app.services.http_pool import close_http_pool, resolve_http_pool
from app.size_limit import StreamingSizeLimitMiddleware, install_size_limit
from app.web import routes as web_routes

log = get_logger("app")


def _build_middleware(app: FastAPI, settings: Settings) -> None:
    """Attach the correlation/headers middleware + the EngineError handler."""

    @app.middleware("http")
    async def correlation_and_headers(request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        set_request_id(rid)
        t0 = time.perf_counter()
        oversize = _oversize_body(request, settings.max_request_bytes)
        if oversize is not None:
            return oversize
        response = await _call_next_safe(request, call_next, rid)
        _apply_headers(response, rid, settings)
        log_event(log, 20, "request",
                  method=request.method, path=request.url.path,
                  status=response.status_code,
                  latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        return response

    @app.exception_handler(EngineError)
    async def engine_error_handler(request: Request, exc: EngineError):
        log_event(log, 40, "engine request failed", engine=exc.engine,
                  error=exc.error, request_id=get_request_id(),
                  detail=exc.detail[:200])
        return JSONResponse(
            {"detail": {
                "error": exc.error,
                "message": f"{exc.engine} is currently unavailable.",
                "request_id": get_request_id(),
            }},
            status_code=exc.status_code)


def _oversize_body(request: Request, max_bytes: int) -> JSONResponse | None:
    """413 gate on `Content-Length` (chunked bodies are bounded at the
    ASGI layer by `app.size_limit`)."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                return JSONResponse(
                    {"error": "request_too_large",
                     "message": "Request body exceeds the maximum allowed size.",
                     "request_id": get_request_id()},
                    status_code=413)
        except (ValueError, TypeError):
            pass
    return None


async def _call_next_safe(request: Request, call_next, rid: str):
    """Run the inner app once. Returns the normal Response, or a
    controlled 500 when an unhandled exception escaped (logged as
    `internal_error`, no stack trace to the client)."""
    from fastapi import HTTPException as FastAPIHTTPException
    try:
        return await call_next(request)
    except FastAPIHTTPException:
        raise  # FastAPI's own handler produces the 4xx response.
    except Exception as exc:  # noqa: BLE001 — last-resort, logged only
        log_event(log, 40, "unhandled exception", path=request.url.path,
                  exc_type=type(exc).__name__, exc_str=str(exc)[:200])
        return JSONResponse(
            {"error": "internal_error",
             "message": "Internal server error.",
             "request_id": rid},
            status_code=500)


def _apply_headers(response, rid: str, settings: Settings) -> None:
    response.headers["X-Request-ID"] = rid
    for k, v in SECURE_HEADERS.items():
        response.headers.setdefault(k, v)
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = \
            "max-age=63072000; includeSubDomains"


def create_app(settings: Settings | None = None) -> StreamingSizeLimitMiddleware:
    """Build the FastAPI app and wrap it with the ASGI streaming cap.

    Returns the ASGI wrapper (callable); the underlying FastAPI instance
    is reachable via `wrapper.user_app` for dependency overrides,
    tests, and uvicorn (which serves any ASGI application).
    """
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    init_db(settings)

    # App-level shared async HTTP pool (connection reuse across requests).
    pool = resolve_http_pool(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await close_http_pool(settings)

    app = FastAPI(
        title="School AI Platform",
        description="API-first frontend for Student Master, Examinations and Results.",
        version="0.1.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.http_pool = pool

    _build_middleware(app, settings)

    for r in (health_routes.router, student_routes.router, exam_routes.router,
              results_routes.router, ai_routes.router, upload_routes.router):
        app.include_router(r)
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")),
              name="static")
    # ASGI-level streaming cap: bounds chunked (no Content-Length) bodies
    # that the HTTP-middleware Content-Length gate cannot see. The wrapper
    # is returned as the ASGI application; the underlying FastAPI stays
    # reachable via `user_app` for dependency overrides and tests.
    return install_size_limit(app, settings.max_request_bytes)


app = create_app()
