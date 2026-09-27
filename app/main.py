"""Application entrypoint — builds the FastAPI app.

Middleware order: correlation ID → secure headers → routers.
"""

from __future__ import annotations

import time
import uuid

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
from app.logging_setup import get_logger, log_event, set_request_id, setup_logging
from app.security import SECURE_HEADERS
from app.services.engine_client import EngineError
from app.web import routes as web_routes

log = get_logger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    init_db(settings)

    app = FastAPI(
        title="School AI Platform",
        description="API-first frontend for Student Master, Examinations and Results.",
        version="0.1.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
    )
    app.state.settings = settings

    @app.middleware("http")
    async def correlation_and_headers(request: Request, call_next):
        from fastapi import HTTPException as FastAPIHTTPException
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        set_request_id(rid)
        t0 = time.perf_counter()
        # Request size gate — reject oversized bodies before they are read.
        try:
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.max_request_bytes:
                return JSONResponse({"detail": "Request body too large",
                                     "request_id": rid}, status_code=413)
        except (ValueError, TypeError):
            pass
        try:
            response = await call_next(request)
        except FastAPIHTTPException:
            raise  # handled by FastAPI's own exception handlers
        except Exception:  # noqa: BLE001 — last-resort handler: log safe metadata only
            log_event(log, 40, "unhandled exception", path=request.url.path)
            return JSONResponse({"detail": "Internal server error",
                                 "request_id": rid}, status_code=500)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURE_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = \
                "max-age=63072000; includeSubDomains"
        log_event(log, 20, "request",
                  method=request.method, path=request.url.path,
                  status=response.status_code,
                  latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        return response

    @app.exception_handler(EngineError)
    async def engine_error_handler(request: Request, exc: EngineError):
        return JSONResponse({"detail": str(exc), "engine": exc.engine},
                            status_code=exc.status_code)

    for r in (health_routes.router, student_routes.router, exam_routes.router,
              results_routes.router, ai_routes.router, upload_routes.router):
        app.include_router(r)
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    return app


app = create_app()
