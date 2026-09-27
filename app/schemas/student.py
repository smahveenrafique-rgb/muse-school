"""Typed request/response models for the Student Master API."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Stable identifier format: e.g. APS-2026-0042. Human-readable, sortable,
# safe to embed in URLs and engine payloads.
CODE_RE = re.compile(r"^[A-Z]{2,6}-\d{4}-\d{3,6}$")
# Pakistani mobile formats accepted: 03XXXXXXXXX or +92XXXXXXXXXX.
MOBILE_RE = re.compile(r"^(?:\+92|0)?3\d{9}$")


class StudentBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    father_name: str = Field(default="", max_length=120)
    class_name: str = Field(min_length=1, max_length=40)
    section: str = Field(default="", max_length=10)
    mobile: str = Field(default="", max_length=16)
    status: Literal["active", "inactive"] = "active"

    @field_validator("full_name", "father_name")
    @classmethod
    def _strip_names(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if v and not MOBILE_RE.match(v):
            raise ValueError("mobile must look like 03XXXXXXXXX or +92XXXXXXXXXX")
        return v


class StudentCreate(StudentBase):
    """student_code is server-generated; clients never invent identity."""


class StudentUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    father_name: str | None = Field(default=None, max_length=120)
    class_name: str | None = Field(default=None, min_length=1, max_length=40)
    section: str | None = Field(default=None, max_length=10)
    mobile: str | None = Field(default=None, max_length=16)
    status: Literal["active", "inactive"] | None = None

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().replace(" ", "").replace("-", "")
        if v and not MOBILE_RE.match(v):
            raise ValueError("mobile must look like 03XXXXXXXXX or +92XXXXXXXXXX")
        return v


class StudentOut(StudentBase):
    id: int
    student_code: str
    created_at: str
    updated_at: str


class StudentListOut(BaseModel):
    items: list[StudentOut]
    total: int
    page: int
    page_size: int


class StudentImportRow(BaseModel):
    """One row of a bulk student import (CSV/Excel normalised upstream)."""
    full_name: str = Field(min_length=2, max_length=120)
    father_name: str = Field(default="", max_length=120)
    class_name: str = Field(min_length=1, max_length=40)
    section: str = Field(default="", max_length=10)
    mobile: str = Field(default="", max_length=16)

    @field_validator("mobile")
    @classmethod
    def _validate_mobile(cls, v: str) -> str:
        v = v.strip().replace(" ", "").replace("-", "")
        if v and not MOBILE_RE.match(v):
            raise ValueError("mobile must look like 03XXXXXXXXX or +92XXXXXXXXXX")
        return v


class StudentImportResult(BaseModel):
    created: int
    skipped: int
    duplicates: int = 0
    errors: list[str] = []
