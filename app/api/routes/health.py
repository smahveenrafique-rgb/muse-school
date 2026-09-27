"""Health and readiness probes.

Two levels:
- /api/v1/health — cheap liveness probe (no engine calls).
- /api/v1/ready — full readiness: database, student counts, engine health
  probes, AI provider status, and an explicit stub_mode flag. The dashboard
  and Settings page render from this; nothing is fabricated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.gateway import AIGateway
from app.config import Settings, get_settings
from app.db import db_session
from app.logging_setup import get_request_id
from app.security import require_admin
from app.services.engine_client import ExamEngineClient, ResultsEngineClient
from app.services.students import StudentService

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "school-ai-platform"}


@router.get("/api/v1/ready")
def ready(settings: Settings = Depends(get_settings)):
    request_id = get_request_id()
    gateway = AIGateway(settings)
    chain = gateway._build_chain()
    exam_health = ExamEngineClient(settings).health(request_id=request_id)
    results_health = ResultsEngineClient(settings).health(request_id=request_id)
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
