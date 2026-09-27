"""Security tests: auth gates, login flow, header hygiene, secret hygiene."""

from __future__ import annotations

from conftest import TEST_TOKEN


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
    r = client.post("/login", data={"token": TEST_TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers.get("set-cookie", "")
    assert "admin_session=" in cookie and "HttpOnly" in cookie


def test_login_rejects_bad_token(client):
    r = client.post("/login", data={"token": "nope"})
    assert r.status_code == 200
    assert "Invalid token" in r.text


def test_cookie_session_grants_access(client):
    client.post("/login", data={"token": TEST_TOKEN})
    assert client.get("/api/v1/students").status_code == 200


def test_logout_revokes_session(client):
    client.post("/login", data={"token": TEST_TOKEN})
    assert client.get("/api/v1/students").status_code == 200
    client.post("/logout")
    assert client.get("/api/v1/students").status_code == 401


def test_secure_headers_present(client):
    r = client.get("/api/v1/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]


def test_settings_page_leaks_no_secrets(client, settings):
    client.post("/login", data={"token": TEST_TOKEN})
    html = client.get("/settings").text
    assert TEST_TOKEN not in html
    assert settings.app_secret_key not in html
    assert "api_key" not in html.lower()


def test_request_id_correlation(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "corr-123"})
    assert r.headers["X-Request-ID"] == "corr-123"


def test_health_is_public(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
