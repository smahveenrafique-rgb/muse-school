"""AI task API — the browser's only door to the AI Gateway.

The gateway runs server-side; provider keys never reach the browser.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.gateway import AIGateway, AIServiceError, GatewayResult
from app.ai.prompts import allowed_contexts, allowed_tasks
from app.config import Settings, get_settings
from app.logging_setup import get_request_id
from app.security import require_admin

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])

AIContext = Literal["CAT-1", "CAT-2", "FINAL", "RESULTS", "GENERAL"]


class AITaskRequest(BaseModel):
    task: str = Field(description="One of the gateway's allowed tasks")
    context: AIContext = Field(description="Routes the system prompt; no cross-context leakage")
    input: str = Field(min_length=1, max_length=8000)


class AITaskOut(BaseModel):
    ok: bool
    task: str
    data: dict = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    error: str = ""


@router.get("/tasks", response_model=list[str])
def list_tasks():
    return allowed_tasks()


@router.get("/contexts", response_model=list[str])
def list_contexts(task: str = ""):
    """Contexts valid for a task (all five when no task is given)."""
    return allowed_contexts(task or None)


@router.post("/run", response_model=AITaskOut, dependencies=[Depends(require_admin)])
def run_ai_task(payload: AITaskRequest, settings: Settings = Depends(get_settings)):
    request_id = get_request_id()
    gateway = AIGateway(settings)
    try:
        result: GatewayResult = gateway.run(task=payload.task,
                                            context=payload.context,
                                            user_input=payload.input,
                                            request_id=request_id)
    except (ValueError, AIServiceError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.error)
    return AITaskOut(ok=True, task=result.task, data=result.data,
                     provider=result.provider, model=result.model)
