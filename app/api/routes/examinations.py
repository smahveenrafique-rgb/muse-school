"""Examinations API — user-friendly workflows over the Exam Engine.

The platform NEVER decides exam business rules. It collects the operator's
choices, forwards a structured request to the Exam Engine, and reports the
job status back. Marks, patterns, and totals are engine territory.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.db import db_session
from app.logging_setup import get_logger, get_request_id, log_event
from app.schemas.engines import ExamJobOut, ExamJobRequest, ExamType
from app.security import require_admin
from app.services.engine_client import EngineError, ExamEngineClient
from app.services.history import list_exam_jobs, record_exam_job
from app.services.http_pool import resolve_http_pool

router = APIRouter(prefix="/api/v1/examinations", tags=["examinations"])

log = get_logger("api")


def _client(settings: Settings = Depends(get_settings)) -> ExamEngineClient:
    """Engine client bound to the app-level shared HTTP pool."""
    client = ExamEngineClient(settings)
    client.set_pool(resolve_http_pool(settings))
    return client


def _raise_engine_error(exc: EngineError) -> None:
    """Convert a controlled engine failure into a 502 with a safe message.

    Upstream response bodies are logged (with correlation id) but never
    forwarded to the client — no engine stack-trace leakage.
    """
    log_event(log, 40, "engine request failed", engine=exc.engine,
              error=exc.error, request_id=get_request_id(),
              detail=exc.detail[:200])
    raise HTTPException(
        status_code=502,
        detail={
            "error": exc.error,
            "message": f"{exc.engine} is currently unavailable.",
            "request_id": get_request_id(),
        },
    ) from exc


@router.get("/types", response_model=list[str])
def exam_types():
    return ["CAT-1", "CAT-2", "FINAL"]


@router.post("/jobs", response_model=ExamJobOut, dependencies=[Depends(require_admin)])
async def submit_exam_job(payload: ExamJobRequest,
                          settings: Settings = Depends(get_settings),
                          client: ExamEngineClient = Depends(_client)):
    """Ask the Exam Engine to start an exam-generation job.

    The submission is recorded in platform history (orchestration metadata
    only) so operators have an Examination History view.
    """
    request_id = get_request_id()
    try:
        data = await client.submit_job(payload.model_dump(), request_id)
        out = ExamJobOut(**data)
    except EngineError as exc:
        _raise_engine_error(exc)
    with db_session(settings) as conn:
        record_exam_job(conn, request_id=request_id, actor="admin",
                        job_id=out.job_id, exam_type=out.exam_type,
                        class_name=payload.class_name,
                        subject=payload.subject or "",
                        status=out.status)
    return out


@router.get("/history", response_model=list[dict])
def exam_job_history(settings: Settings = Depends(get_settings),
                     limit: int = 50,
                     _auth=Depends(require_admin)):
    """Platform-side examination history (submitted jobs)."""
    with db_session(settings) as conn:
        return list_exam_jobs(conn, limit=min(limit, 200))


@router.get("/jobs/{job_id}", response_model=ExamJobOut,
            dependencies=[Depends(require_admin)])
async def get_exam_job(job_id: str, client: ExamEngineClient = Depends(_client)):
    request_id = get_request_id()
    try:
        data = await client.get_job(job_id, request_id)
        return ExamJobOut(**data)
    except EngineError as exc:
        _raise_engine_error(exc)


def exam_type_hint(exam_type: ExamType) -> str:
    return {
        "CAT-1": "First monthly class test",
        "CAT-2": "Second monthly class test",
        "FINAL": "Annual examination",
    }[exam_type]
