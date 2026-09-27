"""Strict output schemas for AI responses.

Every gateway result is validated against one of these before it reaches
any caller. Validation failure => reject, never repair-and-accept.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineSection(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    points: list[str] = Field(min_length=1, max_length=30)


class ExamOutline(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[OutlineSection] = Field(min_length=1, max_length=12)
    notes: str = Field(default="", max_length=2000)


class ResultsSummary(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    highlights: list[str] = Field(default_factory=list, max_length=10)
    concerns: list[str] = Field(default_factory=list, max_length=10)


class ParentMessage(BaseModel):
    message: str = Field(min_length=1, max_length=600)
    tone: str = Field(default="formal", pattern="^(formal|warm)$")


class AIErrorPayload(BaseModel):
    """The model's only legal way to report missing input."""
    error: str = Field(pattern="^missing_input$")
    needed: list[str] = Field(min_length=1)


TASK_SCHEMAS: dict[str, type[BaseModel]] = {
    "draft_exam_outline": ExamOutline,
    "summarize_results": ResultsSummary,
    "parent_message": ParentMessage,
}


def validate_task_output(task: str, data: dict) -> BaseModel:
    """Validate parsed JSON against the task schema. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("AI output is not a JSON object")
    if data.get("error") == "missing_input":
        # Legal refusal shape — surface it as a controlled error, not data.
        needed = data.get("needed", [])
        raise ValueError(f"model reports missing input: {needed}")
    schema = TASK_SCHEMAS.get(task)
    if schema is None:
        raise ValueError(f"unknown AI task: {task}")
    return schema.model_validate(data)
