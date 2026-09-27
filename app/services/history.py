"""Platform-side orchestration metadata: exam job history, result query
history, and the recent-activity feed.

These record *platform events* (a job was submitted, a result was queried)
— never engine business data. Marks/totals/grades stay engine-owned.
"""

from __future__ import annotations

import sqlite3

__all__ = [
    "list_exam_jobs",
    "list_result_queries",
    "recent_activity",
    "record_exam_job",
    "record_result_query",
]


def record_exam_job(conn: sqlite3.Connection, *, request_id: str, actor: str,
                    job_id: str, exam_type: str, class_name: str,
                    subject: str = "", status: str = "submitted") -> None:
    conn.execute(
        """INSERT INTO exam_job_submissions
               (request_id, actor, job_id, exam_type, class_name, subject, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (request_id, actor, job_id, exam_type, class_name, subject, status),
    )


def list_exam_jobs(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT submitted_at, request_id, actor, job_id, exam_type,
                  class_name, subject, status
           FROM exam_job_submissions
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def record_result_query(conn: sqlite3.Connection, *, request_id: str, actor: str,
                        scope: str, class_name: str = "", subject: str = "",
                        student_code: str = "", item_count: int = 0,
                        source: str = "") -> None:
    conn.execute(
        """INSERT INTO result_query_log
               (request_id, actor, scope, class_name, subject, student_code,
                item_count, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (request_id, actor, scope, class_name, subject, student_code,
         item_count, source),
    )


def list_result_queries(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT queried_at, request_id, actor, scope, class_name, subject,
                  student_code, item_count, source
           FROM result_query_log
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_activity(conn: sqlite3.Connection, limit: int = 25) -> list[dict]:
    """Safe activity feed — audit metadata only, no student PII."""
    rows = conn.execute(
        """SELECT ts, actor, action, entity, entity_id
           FROM audit_log
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
