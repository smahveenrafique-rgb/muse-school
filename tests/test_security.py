"""Security tests: auth gates, login flow, header hygiene, secret hygiene."""

from __future__ import annotations

import re

from conftest import TEST_TOKEN


def _get_csrf(client) -> str:
    """Fetch the CSRF token embedded in the login page form."""
    page = client.get("/login")
    m = re.search(r'name="csrf_token" value="([a-f0-9]+)"', page.text)
    assert m, "csrf token not found in login page"
    return m.group(1)


def test_api_rejects_missing_token(client):
    assert client.get("/api/v1/students").status_code == 401
    assert client.get("/api/v1/results?scope=CAT-1&class=5").status_code == 401


def test_api_rejects_wrong_token(client):
    r = client.get("/api/v1/students", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_pages_require_login(client):
    for path in ["/", "/students", "/examinations", "/results", "/settings"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (401, 307, 303), path


def test_login_flow_sets_httponly_cookie(client):
    csrf = _get_csrf(client)
    r = client.post("/login", data={"token": TEST_TOKEN, "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers.get("set-cookie", "")
    assert "admin_session=" in cookie and "HttpOnly" in cookie


def test_login_rejects_bad_token(client):
    csrf = _get_csrf(client)
    r = client.post("/login", data={"token": "nope", "csrf_token": csrf})
    assert r.status_code == 200
    assert "Invalid token" in r.text


def test_login_rejects_missing_csrf(client):
    r = client.post("/login", data={"token": TEST_TOKEN})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_login_rejects_forged_csrf(client):
    r = client.post("/login", data={"token": TEST_TOKEN, "csrf_token": "forged"})
    assert r.status_code == 403


def test_cookie_session_grants_access(client):
    csrf = _get_csrf(client)
    client.post("/login", data={"token": TEST_TOKEN, "csrf_token": csrf})
    assert client.get("/api/v1/students").status_code == 200


def test_logout_revokes_session(client):
    csrf = _get_csrf(client)
    client.post("/login", data={"token": TEST_TOKEN, "csrf_token": csrf})
    assert client.get("/api/v1/students").status_code == 200
    client.post("/logout")
    assert client.get("/api/v1/students").status_code == 401


def test_login_rate_limit_enforced(client, settings):
    """Brute-force backstop: N+1 attempts within the window are rejected."""
    settings.login_max_attempts = 3
    settings.login_window_seconds = 3600
    csrf = _get_csrf(client)
    for _ in range(3):
        r = client.post("/login", data={"token": "wrong", "csrf_token": csrf})
        assert r.status_code in (200, 403), r.text
    r = client.post("/login",
                    data={"token": TEST_TOKEN, "csrf_token": csrf})
    assert r.status_code == 429
    assert "Too many" in r.json()["detail"]


def test_secure_headers_present(client):
    r = client.get("/api/v1/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_settings_page_leaks_no_secrets(client, settings):
    csrf = _get_csrf(client)
    client.post("/login", data={"token": TEST_TOKEN, "csrf_token": csrf})
    html = client.get("/settings").text
    assert TEST_TOKEN not in html
    assert settings.app_secret_key not in html
    assert "api_key" not in html.lower()


def test_request_id_correlation(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "corr-123"})
    assert r.headers["X-Request-ID"] == "corr-123"


def test_health_is_public(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_session_ttl_enforced(client, settings):
    """A session created with a 1-second TTL must expire immediately."""
    settings.session_ttl_seconds = 1
    csrf = _get_csrf(client)
    client.post("/login", data={"token": TEST_TOKEN, "csrf_token": csrf})
    import time
    time.sleep(1.2)
    assert client.get("/api/v1/students").status_code == 401
