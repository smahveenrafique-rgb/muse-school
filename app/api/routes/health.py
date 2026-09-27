"""Health and readiness probes.

Two levels:
- /api/v1/health — cheap liveness probe (no engine calls, no DB).
- /api/v1/ready — full readiness: database, student counts, engine health
  probes (concurrent, bounded timeout), AI provider status, and an
  explicit stub_mode flag. The dashboard and Settings page render from
  this; nothing is fabricated.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.ai.gateway import AIGateway
from app.config import Settings, get_settings
from app.db import db_session
from app.logging_setup import get_request_id
from app.security import require_admin
from app.services.engine_client import ExamEngineClient, ResultsEngineClient
from app.services.http_pool import close_http_pool, resolve_http_pool
from app.services.students import StudentService

router = APIRouter(tags=["health"])

# /ready probes the engines concurrently and gives up quickly — readiness
# must never hang. 2 s is generous for LAN engines, tight for dashboards.
_READY_PROBE_TIMEOUT_SECONDS = 2.0


@router.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "school-ai-platform"}


@router.get("/api/v1/ready")
async def ready(settings: Settings = Depends(get_settings)):
    request_id = get_request_id()
    created_here = getattr(settings, "_http_pool", None) is None
    pool = resolve_http_pool(settings)
    gateway = AIGateway(settings, http_pool=pool)
    chain = gateway._build_chain()

    exam_client = ExamEngineClient(settings)
    results_client = ResultsEngineClient(settings)
    exam_client.set_pool(pool)
    results_client.set_pool(pool)

    async def _probe(client, name: str) -> dict:
        try:
            return await asyncio.wait_for(
                client.health(request_id=request_id),
                timeout=_READY_PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return {"status": "unreachable", "engine": name,
                    "latency_ms": int(_READY_PROBE_TIMEOUT_SECONDS * 1000),
                    "detail": "health probe timed out"}

    exam_health, results_health = await asyncio.gather(
        _probe(exam_client, "exam-engine"),
        _probe(results_client, "results-engine"),
    )
    # The shared pool is app-level and owned by the app shutdown hook.
    # Standalone callers (no app, e.g. scripts) created it here and must
    # release it — including the private pools any providers own.
    if created_here:
        await close_http_pool(settings)
        await gateway.aclose()
    counts = StudentService(settings).counts()
    return {
        "status": "ok",
        "service": "school-ai-platform",
        "environment": settings.app_env,
        "database": str(settings.database_path),
        "stub_mode": settings.stub_mode,
        "students": counts,
        "engines": {
            "exam": exam_health,
            "results": results_health,
        },
        "ai": {
            "providers_configured": len(chain),
            "stub_mode": settings.ai_use_stub or not chain,
            "providers": [p.describe()["provider"] for p in chain],
        },
    }


@router.get("/api/v1/activity", dependencies=[Depends(require_admin)])
def activity(settings: Settings = Depends(get_settings), limit: int = 25):
    """Recent platform activity (audit metadata only — no student PII)."""
    from app.services.history import recent_activity
    with db_session(settings) as conn:
        return recent_activity(conn, limit=min(limit, 100))
