"""Student Master API — /api/v1/students.

Reads are admin-gated too: student PII never leaves the server without auth.
DELETE is refused (405) — deactivation via status is the only removal path.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.config import Settings, get_settings
from app.schemas.student import (
    StudentCreate,
    StudentImportResult,
    StudentImportRow,
    StudentListOut,
    StudentOut,
    StudentUpdate,
)
from app.security import require_admin
from app.services.students import StudentService

router = APIRouter(prefix="/api/v1/students", tags=["students"])


def _svc(settings: Settings = Depends(get_settings)) -> StudentService:
    return StudentService(settings)


@router.get("", response_model=StudentListOut, dependencies=[Depends(require_admin)])
def list_students(
    q: str = Query(default=""),
    class_name: str = Query(default="", alias="class"),
    status_: str = Query(default="", alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    svc: StudentService = Depends(_svc),
):
    return svc.list_students(q=q, class_name=class_name, status=status_,
                             page=page, page_size=page_size)


@router.post("", response_model=StudentOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)])
def create_student(data: StudentCreate, svc: StudentService = Depends(_svc)):
    return svc.create(data)


@router.get("/classes", response_model=list[str], dependencies=[Depends(require_admin)])
def list_classes(svc: StudentService = Depends(_svc)):
    return svc.distinct_classes()


@router.get("/{student_id}", response_model=StudentOut, dependencies=[Depends(require_admin)])
def get_student(student_id: int, svc: StudentService = Depends(_svc)):
    student = svc.get(student_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.patch("/{student_id}", response_model=StudentOut, dependencies=[Depends(require_admin)])
def update_student(student_id: int, data: StudentUpdate, svc: StudentService = Depends(_svc)):
    student = svc.update(student_id, data)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    return student


@router.delete("/{student_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
               dependencies=[Depends(require_admin)])
def delete_student():
    raise HTTPException(
        status_code=405,
        detail="Students are never hard-deleted. Set status to 'inactive' instead.",
    )


@router.post("/import-rows", response_model=StudentImportResult,
             dependencies=[Depends(require_admin)])
def import_student_rows(rows: list = Body(...), svc: StudentService = Depends(_svc)):
    """Bulk import from already-validated rows (used by the upload flow)."""
    parsed = [StudentImportRow(**r) for r in rows]
    return svc.import_rows(parsed)
