"""Student Master service — the only writer/reader of the students table.

Identity rule: `student_code` is generated server-side as APS-<year>-<seq>
and never changes. Engines reference students by this code.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from app.config import Settings
from app.db import db_session
from app.schemas.student import (
    StudentCreate,
    StudentImportResult,
    StudentImportRow,
    StudentListOut,
    StudentOut,
    StudentUpdate,
)

# Alias: StudentService.list shadows the `list` builtin for annotations
# inside the class body, so later methods use this alias instead.
_BuiltinList = list


def _row_to_out(row: sqlite3.Row) -> StudentOut:
    return StudentOut(**dict(row))


def _next_code(conn: sqlite3.Connection, year: int) -> str:
    prefix = f"APS-{year}-"
    row = conn.execute(
        "SELECT student_code FROM students WHERE student_code LIKE ? "
        "ORDER BY student_code DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    seq = int(row["student_code"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"{prefix}{seq:04d}"


def _audit(conn: sqlite3.Connection, action: str, entity_id: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, detail) "
        "VALUES ('admin', ?, 'student', ?, ?)",
        (action, entity_id, detail[:500]),
    )


class StudentService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def create(self, data: StudentCreate) -> StudentOut:
        with db_session(self.settings) as conn:
            year = datetime.now(UTC).year
            code = _next_code(conn, year)
            cur = conn.execute(
                """INSERT INTO students
                   (student_code, full_name, father_name, class_name, section, mobile, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (code, data.full_name, data.father_name, data.class_name,
                 data.section, data.mobile, data.status),
            )
            _audit(conn, "create", code, data.full_name)
            row = conn.execute("SELECT * FROM students WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_out(row)

    def get(self, student_id: int) -> StudentOut | None:
        with db_session(self.settings) as conn:
            row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
            return _row_to_out(row) if row else None

    def get_by_code(self, code: str) -> StudentOut | None:
        with db_session(self.settings) as conn:
            row = conn.execute("SELECT * FROM students WHERE student_code = ?", (code,)).fetchone()
            return _row_to_out(row) if row else None

    def list(self, *, q: str = "", class_name: str = "", status: str = "",
             page: int = 1, page_size: int = 25) -> StudentListOut:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        clauses, params = [], []
        if q:
            clauses.append("(full_name LIKE ? OR student_code LIKE ? OR father_name LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like]
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if status in ("active", "inactive"):
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with db_session(self.settings) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM students {where}", params
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM students {where} ORDER BY class_name, full_name "
                f"LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()
            return StudentListOut(
                items=[_row_to_out(r) for r in rows],
                total=total, page=page, page_size=page_size,
            )

    def update(self, student_id: int, data: StudentUpdate) -> StudentOut | None:
        patch = data.model_dump(exclude_unset=True)
        if not patch:
            return self.get(student_id)
        patch["updated_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sets = ", ".join(f"{k} = ?" for k in patch)
        with db_session(self.settings) as conn:
            cur = conn.execute(f"UPDATE students SET {sets} WHERE id = ?",
                               (*patch.values(), student_id))
            if cur.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
            _audit(conn, "update", row["student_code"], ",".join(patch))
            return _row_to_out(row)

    def set_status(self, student_id: int, status: str) -> StudentOut | None:
        return self.update(student_id, StudentUpdate(status=status))  # type: ignore[arg-type]

    def delete(self, student_id: int) -> bool:
        """Hard delete is intentionally unavailable via service — use status.

        This method exists only so the API can return 405 with guidance.
        """
        return False

    def import_rows(self, rows: _BuiltinList[StudentImportRow]) -> StudentImportResult:
        created = skipped = duplicates = 0
        errors: _BuiltinList[str] = []
        with db_session(self.settings) as conn:
            year = datetime.now(UTC).year
            for i, r in enumerate(rows, start=1):
                try:
                    name = r.full_name.strip()
                    father = r.father_name.strip()
                    klass = r.class_name.strip()
                    section = r.section.strip()
                    # Idempotency guard: a retried import must not duplicate
                    # students. Natural key = name + father/guardian + class
                    # + section (case-insensitive). Matches are skipped as
                    # duplicates, not inserted.
                    exists = conn.execute(
                        """SELECT 1 FROM students
                           WHERE lower(full_name) = lower(?)
                             AND lower(father_name) = lower(?)
                             AND lower(class_name) = lower(?)
                             AND lower(section) = lower(?)""",
                        (name, father, klass, section),
                    ).fetchone()
                    if exists:
                        duplicates += 1
                        continue
                    code = _next_code(conn, year)
                    conn.execute(
                        """INSERT INTO students
                           (student_code, full_name, father_name, class_name, section, mobile)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (code, name, father, klass, section, r.mobile.strip()),
                    )
                    _audit(conn, "import", code, name)
                    created += 1
                except sqlite3.IntegrityError:
                    skipped += 1
                except Exception as exc:  # noqa: BLE001 — collected per-row
                    errors.append(f"row {i}: {exc}")
        return StudentImportResult(created=created, skipped=skipped,
                                   duplicates=duplicates, errors=errors)

    def counts(self) -> dict[str, int]:
        """Real student counts for the dashboard — no fabrication."""
        with db_session(self.settings) as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,
                          SUM(CASE WHEN status = 'inactive' THEN 1 ELSE 0 END) AS inactive
                   FROM students""").fetchone()
        return {"total": row["total"] or 0,
                "active": row["active"] or 0,
                "inactive": row["inactive"] or 0}

    def distinct_classes(self) -> _BuiltinList[str]:
        with db_session(self.settings) as conn:
            rows = conn.execute(
                "SELECT DISTINCT class_name FROM students ORDER BY class_name").fetchall()
            return [r["class_name"] for r in rows]
