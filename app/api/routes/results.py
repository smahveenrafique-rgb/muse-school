"""Results API — read-only workflows over the Results Engine.

The Results Engine is the source of truth for every number. This router
queries, validates the shape, and passes values through untouched — it
never calculates marks, percentages, totals, or pass/fail.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.db import db_session
from app.logging_setup import get_logger, get_request_id, log_event
from app.schemas.engines import ResultListOut, ResultQuery, ResultRow
from app.security import require_admin
from app.services.engine_client import EngineError, ResultsEngineClient
from app.services.history import list_result_queries, record_result_query
from app.services.http_pool import resolve_http_pool

router = APIRouter(prefix="/api/v1/results", tags=["results"])

log = get_logger("api")


def _client(settings: Settings = Depends(get_settings)) -> ResultsEngineClient:
    """Results client bound to the app-level shared HTTP pool."""
    client = ResultsEngineClient(settings)
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


@router.get("", response_model=ResultListOut, dependencies=[Depends(require_admin)])
async def query_results(
    scope: str = Query(default="CAT-1", pattern="^(CAT-1|CAT-2|FINAL|CONSOLIDATED)$"),
    class_name: str = Query(default=..., min_length=1, max_length=40, alias="class"),
    subject: str = Query(default=""),
    student_code: str = Query(default=""),
    settings: Settings = Depends(get_settings),
    client: ResultsEngineClient = Depends(_client),
):
    query = ResultQuery(scope=scope, class_name=class_name,  # type: ignore[arg-type]
                        subject=subject, student_code=student_code)
    request_id = get_request_id()
    try:
        data = await client.query(query.model_dump(), request_id)
    except EngineError as exc:
        _raise_engine_error(exc)
    items = [ResultRow(**row) for row in data.get("items", [])]
    out = ResultListOut(items=items,
                        generated_at=str(data.get("generated_at", "")),
                        source=str(data.get("source", "results-engine")))
    # Record the query (metadata only — never the rows themselves) so the
    # platform has a Result History view.
    with db_session(settings) as conn:
        record_result_query(conn, request_id=request_id, actor="admin",
                            scope=scope, class_name=class_name,
                            subject=subject, student_code=student_code,
                            item_count=len(items), source=out.source)
    return out


@router.get("/history", response_model=list[dict])
def result_query_history(settings: Settings = Depends(get_settings),
                        limit: int = 50,
                        _auth=Depends(require_admin)):
    """Platform-side result query history (query metadata, not rows)."""
    with db_session(settings) as conn:
        return list_result_queries(conn, limit=min(limit, 200))
