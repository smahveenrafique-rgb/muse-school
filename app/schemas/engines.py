"""Typed models for the upstream engine APIs.

These are the platform's *expectations* of the Exam and Results engines —
the stable API boundary. The platform never computes exam or result
business rules; it only sends structured requests and renders responses.

Full contract documentation: docs/API.md
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExamType = Literal["CAT-1", "CAT-2", "FINAL"]
ResultScope = Literal["CAT-1", "CAT-2", "FINAL", "CONSOLIDATED"]


# ── Exam System ────────────────────────────────────────────────────────────

class ExamJobRequest(BaseModel):
    exam_type: ExamType
    class_name: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=80)
    # Opaque, engine-defined options (e.g. paper template id). The platform
    # passes them through without interpreting them.
    options: dict = Field(default_factory=dict)
    # Student codes the paper roster must cover (empty = whole class).
    student_codes: list[str] = Field(default_factory=list, max_length=500)


class ExamJobOut(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    exam_type: ExamType
    detail: str = ""
    artifact_url: str = ""  # download link when succeeded; engine-hosted


# ── Results System ─────────────────────────────────────────────────────────

class ResultRow(BaseModel):
    student_code: str
    student_name: str = ""
    class_name: str = ""
    subject: str = ""
    exam_type: str = ""
    # All numeric outcomes are engine-computed. The platform treats them as
    # opaque display values and NEVER recomputes them.
    marks_obtained: float | None = None
    total_marks: float | None = None
    percentage: float | None = None
    grade: str = ""
    result: str = ""  # e.g. PASS / FAIL — engine's determination


class ResultQuery(BaseModel):
    scope: ResultScope
    class_name: str = Field(min_length=1, max_length=40)
    subject: str = ""
    student_code: str = ""


class ResultListOut(BaseModel):
    items: list[ResultRow]
    generated_at: str = ""
    source: str = "results-engine"  # which backend produced this payload
