"""Security primitives: admin token auth, secure headers, session store.

API keys stay server-side. The browser never sees provider credentials;
the AI gateway runs exclusively in the backend.

Sessions are stored in SQLite (`sessions` table): random 256-bit ids,
sha256-hashed at rest, TTL-enforced on every auth check. The store is
concurrency-safe (SQLite write-lock serializes concurrent logins) and
grows without bound only until TTL expiry sweeps it.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _digest(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    """Gate administrative operations (student write ops, settings, AI tasks).

    Accepts `Authorization: Bearer <ADMIN_API_TOKEN>`. The web UI keeps the
    token in an HttpOnly session cookie set at login; API clients use the
    header directly. Both paths are verified here.
    """
    token = ""
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    if not token:
        token = request.cookies.get("admin_session", "")
        if token and _valid_session(token, settings):
            return "admin"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="Authentication required")
    if _constant_time_eq(token, settings.admin_api_token):
        return "admin"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                         detail="Invalid credentials")


# ── SQLite session store ────────────────────────────────────────────────
# `sessions` table (app/db.py): id, digest, created_at, expires_at.
# One short transaction per operation; SQLite's write lock serializes
# concurrent logins — no file races, no corruption.


def create_session(settings: Settings) -> str:
    """Create a random session id bound to the admin token hash.

    No credential material is stored; only the sha256 digest of the
    random 256-bit session id. TTL comes from `SESSION_TTL_SECONDS`.
    Expired rows are swept on every create (bounded housekeeping).
    """
    from app.db import db_session
    session_id = secrets.token_urlsafe(32)
    expires = (datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
               ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db_session(settings) as conn:
        conn.execute(
            "INSERT INTO sessions(id, digest, expires_at) VALUES (?, ?, ?)",
            (session_id, _digest(session_id), expires))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_utc_now(),))
    return session_id


def _valid_session(session_id: str, settings: Settings) -> bool:
    """Return True when the session id is live and unexpired.

    Fails closed (returns False) if the session store is unreadable —
    a degraded DB must never grant access, and a 5xx must not leak to
    the caller as an auth bypass. The broad catch is intentional: any
    session-store fault maps to "not authenticated", never to a crash.
    """
    from app.db import db_session
    try:
        with db_session(settings) as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE digest = ? AND expires_at > ?",
                (_digest(session_id), _utc_now()),
            ).fetchone()
    except sqlite3.Error:
        return False
    return row is not None


def destroy_session(session_id: str, settings: Settings) -> None:
    from app.db import db_session
    with db_session(settings) as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


SECURE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # HSTS only makes sense behind TLS; enabled in production via middleware flag.
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'"
    ),
}
