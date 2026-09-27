"""Uploads API — validated file intake (students import for now)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.schemas.student import StudentImportResult, StudentImportRow
from app.security import require_admin
from app.services.students import StudentService
from app.services.uploads import UploadError, parse_student_workbook, validate_upload

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])


@router.post("/students", response_model=StudentImportResult,
             dependencies=[Depends(require_admin)])
async def upload_students(file: UploadFile = File(...),
                          settings: Settings = Depends(get_settings)):
    try:
        upload = await validate_upload(
            file, settings, allowed={"xlsx", "xls", "csv"})
        rows = parse_student_workbook(upload)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        typed_rows = [StudentImportRow(**r) for r in rows]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"row validation failed: {exc}") from exc
    return StudentService(settings).import_rows(typed_rows)
