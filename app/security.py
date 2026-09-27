"""Security primitives: admin token auth, secure headers, upload helpers.

API keys stay server-side. The browser never sees provider credentials;
the AI gateway runs exclusively in the backend.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def _constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


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
        # The cookie stores a random session id; validate against the hash file.
        if token and _valid_session(token):
            return "admin"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authentication required")
    if _constant_time_eq(token, settings.admin_api_token):
        return "admin"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid credentials")


_SESSION_FILE_ENV = "SESSION_FILE"


def _session_file() -> Path:
    from app.config import BASE_DIR
    return BASE_DIR / "data" / ".sessions"


def create_session() -> str:
    """Create a random session id bound to the admin token hash (no token stored)."""
    session_id = secrets.token_urlsafe(32)
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    path = _session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = set(path.read_text().splitlines()) if path.exists() else set()
    existing.add(digest)
    path.write_text("\n".join(sorted(existing)))
    return session_id


def _valid_session(session_id: str) -> bool:
    path = _session_file()
    if not path.exists():
        return False
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return digest in set(path.read_text().splitlines())


def destroy_session(session_id: str) -> None:
    path = _session_file()
    if not path.exists():
        return
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    remaining = [line for line in path.read_text().splitlines() if line != digest]
    path.write_text("\n".join(remaining))


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
