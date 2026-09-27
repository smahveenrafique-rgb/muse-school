"""Server-rendered pages. Thin views only — every page fetches its data
from the JSON API, so the API stays the single source of truth."""

from __future__ import annotations

import time
from collections import deque

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, Settings, get_settings
from app.logging_setup import get_logger, log_event
from app.security import _constant_time_eq, create_session, destroy_session, require_admin

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))

log = get_logger("web")

# ── login brute-force control ──────────────────────────────────────────
# Sliding-window attempt counter per source IP. Memory-bounded: entries
# expire after the window; the whole deque is scanned only on overflow.
# (Reverse-proxy rate limiting is the first line of defence; this is the
# application-level backstop.)
_attempt_windows: dict[str, deque] = {}


def _login_rate_limited(client_ip: str, settings: Settings) -> bool:
    now = time.monotonic()
    window = settings.login_window_seconds
    attempts = _attempt_windows.get(client_ip)
    if attempts is None:
        attempts = deque()
        _attempt_windows[client_ip] = attempts
    while attempts and now - attempts[0] > window:
        attempts.popleft()
    if len(attempts) >= settings.login_max_attempts:
        return True
    attempts.append(now)
    # Bounded memory: drop all expired keys when the table grows.
    if len(_attempt_windows) > 10000:
        for ip in [k for k, q in _attempt_windows.items()
                   if not q or now - q[-1] > window]:
            del _attempt_windows[ip]
    return False


def _csrf_token(request: Request, settings: Settings) -> str:
    """Per-page signed CSRF token. Bound to the request's session cookie.

    The token is signed with `APP_SECRET_KEY` via HMAC-SHA256 (stdlib —
    no new dependency). It is embedded in the login form; a forged
    cross-site form cannot produce a valid signature for the victim's
    context. For /login there is no session yet, so the token is bound
    to the source IP — same site, same browser, no cross-origin.
    """
    import hashlib
    import hmac as _hmac
    session_id = request.cookies.get("admin_session", "")
    ip = request.client.host if request.client else "-"
    return _hmac.new(settings.app_secret_key.encode(),
                     f"csrf:{session_id or ip}".encode(),
                     hashlib.sha256).hexdigest()[:32]


def _csrf_valid(request: Request, token: str, settings: Settings) -> bool:
    import hmac as _hmac
    expected = _csrf_token(request, settings)
    return _hmac.compare_digest(expected, token or "")


def _page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, settings: Settings = Depends(get_settings)):
    return _page(request, "login.html", title="Sign in",
                 csrf_token=_csrf_token(request, settings))


@router.post("/login")
def login(request: Request, token: str = Form(...),
          csrf_token: str = Form(default=""),
          settings: Settings = Depends(get_settings)):
    client_ip = request.client.host if request.client else "unknown"
    if _login_rate_limited(client_ip, settings):
        log_event(log, 30, "login rate limited", client_ip=client_ip)
        raise HTTPException(status_code=429,
                             detail="Too many login attempts. Try again later.")
    if not _csrf_valid(request, csrf_token, settings):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if _constant_time_eq(token, settings.admin_api_token):
        session_id = create_session(settings)
        resp = RedirectResponse("/", status_code=303)
        secure = settings.is_production
        resp.set_cookie("admin_session", session_id, httponly=True,
                        samesite="lax", secure=secure,
                        max_age=settings.session_ttl_seconds)
        return resp
    log_event(log, 30, "login failed", client_ip=client_ip)
    return _page(request, "login.html", title="Sign in", error="Invalid token",
                 csrf_token=_csrf_token(request, settings))


@router.post("/logout")
def logout(request: Request, settings: Settings = Depends(get_settings)):
    session_id = request.cookies.get("admin_session", "")
    if session_id:
        destroy_session(session_id, settings)
    # Logout also clears the login-attempt counter for this client so an
    # operator who signs out and back in is not left on the limit.
    client_ip = request.client.host if request.client else "unknown"
    _attempt_windows.pop(client_ip, None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("admin_session")
    return resp


@router.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def dashboard(request: Request):
    return _page(request, "dashboard.html", title="Dashboard", nav="dashboard")


@router.get("/students", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def students_page(request: Request):
    return _page(request, "students.html", title="Students", nav="students")


@router.get("/examinations", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def examinations_page(request: Request):
    return _page(request, "examinations.html", title="Examinations", nav="examinations")


@router.get("/results", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def results_page(request: Request):
    return _page(request, "results.html", title="Results", nav="results")


@router.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def settings_page(request: Request, settings: Settings = Depends(get_settings)):
    # Show configuration WITHOUT secrets.
    safe = {
        "app_env": settings.app_env,
        "exam_api_base_url": settings.exam_api_base_url,
        "results_api_base_url": settings.results_api_base_url,
        "engine_api_timeout_seconds": settings.engine_api_timeout_seconds,
        "ai_timeout_seconds": settings.ai_timeout_seconds,
        "ai_providers": [t.provider or "(empty)" for t in settings.ai_tiers] or ["(none)"],
        "ai_use_stub": settings.ai_use_stub,
        "upload_max_bytes": settings.upload_max_bytes,
        "database_path": str(settings.database_path),
    }
    return _page(request, "settings.html", title="Settings", nav="settings", config=safe)
