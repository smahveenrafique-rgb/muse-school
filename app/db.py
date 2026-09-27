"""SQLite storage for the Student Master.

stdlib sqlite3 only — no ORM. The student table is the single source of
truth for identity inside this platform; Exam/Results engines reference
students by the stable `student_code`.

Concurrency: WAL mode + `busy_timeout` so concurrent readers/writers
queue instead of failing immediately. The `student_seq` table provides
atomic, transaction-safe sequence allocation: a concurrent create
can never obtain the same number twice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    student_code  TEXT NOT NULL UNIQUE,
    full_name     TEXT NOT NULL,
    father_name   TEXT NOT NULL DEFAULT '',
    class_name    TEXT NOT NULL,
    section       TEXT NOT NULL DEFAULT '',
    mobile        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','inactive')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_students_class ON students(class_name, section);
CREATE INDEX IF NOT EXISTS idx_students_status ON students(status);

-- Atomic per-year sequence allocation. The UPSERT+RETURNING is a single
-- statement: SQLite serializes writers, so two concurrent creates in the
-- same transaction set can never read the same value.
CREATE TABLE IF NOT EXISTS student_seq (
    year     INTEGER PRIMARY KEY,
    next_val INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    actor      TEXT NOT NULL DEFAULT 'admin',
    action     TEXT NOT NULL,
    entity     TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);

-- Server-side session store. Digests the sha256 of the random session id
-- (the cookie value); no credentials are stored. TTL enforced on read.
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    digest     TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- Platform-side orchestration metadata. These tables record *that* the
-- platform submitted/queried something — they never duplicate engine
-- business logic (marks, totals, grades remain engine-owned).
CREATE TABLE IF NOT EXISTS exam_job_submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    submitted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    request_id   TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL DEFAULT 'admin',
    job_id       TEXT NOT NULL,
    exam_type    TEXT NOT NULL,
    class_name   TEXT NOT NULL,
    subject      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'submitted'
);
CREATE INDEX IF NOT EXISTS idx_job_submitted ON exam_job_submissions(submitted_at DESC);

CREATE TABLE IF NOT EXISTS result_query_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    queried_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    request_id   TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL DEFAULT 'admin',
    scope        TEXT NOT NULL,
    class_name   TEXT NOT NULL DEFAULT '',
    subject      TEXT NOT NULL DEFAULT '',
    student_code TEXT NOT NULL DEFAULT '',
    item_count   INTEGER NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_query_time ON result_query_log(queried_at DESC);
"""

# Writers queue up to this long when the DB file is locked by another
# connection — prevents "database is locked" errors under concurrent load.
BUSY_TIMEOUT_MS = 5000


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(settings: Settings) -> None:
    with _connect(settings.database_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def db_session(settings: Settings) -> Iterator[sqlite3.Connection]:
    conn = _connect(settings.database_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
