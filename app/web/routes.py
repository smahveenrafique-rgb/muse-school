"""Server-rendered pages. Thin views only — every page fetches its data
from the JSON API, so the API stays the single source of truth."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, Settings, get_settings
from app.security import create_session, destroy_session, require_admin

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


def _page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, ctx)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _page(request, "login.html", title="Sign in")


@router.post("/login")
def login(request: Request, token: str = Form(...),
          settings: Settings = Depends(get_settings)):
    from app.security import _constant_time_eq
    if _constant_time_eq(token, settings.admin_api_token):
        session_id = create_session()
        resp = RedirectResponse("/", status_code=303)
        secure = settings.is_production
        resp.set_cookie("admin_session", session_id, httponly=True,
                        samesite="lax", secure=secure, max_age=12 * 3600)
        return resp
    return _page(request, "login.html", title="Sign in", error="Invalid token")


@router.post("/logout")
def logout(request: Request):
    session_id = request.cookies.get("admin_session", "")
    if session_id:
        destroy_session(session_id)
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
