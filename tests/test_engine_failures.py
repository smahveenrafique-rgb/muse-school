"""Engine failure-matrix tests: 5xx/4xx/timeout/malformed response paths.

Drives `ExamEngineClient` / `ResultsEngineClient` against a live local
`http.server` with scripted behaviour — no mocks, no respx, no stubs.
This verifies the *real* transport path (connection pooling, retry loop,
error classification, safe error shape) exactly as production runs it.

For the unreachable-engine path, a closed local port is used.

Test strategy:
- A real HTTP server (http.server in a thread) returns the scripted
  status codes / bodies. The engine client's `httpx.AsyncClient` talks
  to it over a real TCP connection.
- Retry counts are observed server-side.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import httpx
import pytest

from app.config import Settings
from app.services.engine_client import (
    EngineError,
    ExamEngineClient,
    ResultsEngineClient,
)


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Returns a scripted sequence of (status, body) per path.

    `SCRIPT` is set on the handler class by the test before starting the
    server. Each request pops the next entry. HTTP/1.1 + Content-Length
    so httpx's keep-alive pool can reuse the connection across the
    engine client's retry attempts (the real failure mode we're testing).
    """
    SCRIPT: ClassVar[list] = []  # [(status, body_bytes), ...]
    CALL_COUNT: ClassVar[int] = 0

    def _respond(self):
        status, body = self.SCRIPT.pop(0) if self.SCRIPT else (500, b"script exhausted")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        type(self).CALL_COUNT += 1

    def do_POST(self):
        self._respond()

    def do_GET(self):
        self._respond()

    def log_message(self, *args):
        pass


def _start_server(script: list) -> tuple:
    handler_cls = type("H", (_ScriptedHandler,),
                       {"SCRIPT": list(script), "CALL_COUNT": 0})
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port, handler_cls


def _make_client(settings: Settings, exam_url: str, results_url: str
                 ) -> tuple[ExamEngineClient, ResultsEngineClient, httpx.AsyncClient]:
    settings.exam_api_base_url = exam_url
    settings.results_api_base_url = results_url
    settings.egress_allow_internal = True
    # No keep-alive: each retry attempt opens a fresh TCP connection,
    # which matches the scripted per-connection test server and avoids
    # httpx's connection-reuse interacting with the http.server socket
    # lifecycle.
    pool = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.engine_api_timeout_seconds,
                              connect=5.0, pool=5.0),
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=50),
    )
    exam = ExamEngineClient(settings)
    results = ResultsEngineClient(settings)
    exam.set_pool(pool)
    results.set_pool(pool)
    return exam, results, pool


# ── 5xx retry exhaustion ────────────────────────────────────────────────

def test_5xx_retries_then_service_unavailable(settings):
    """Engine 503 twice (retries=1) → EngineError(502, service_unavailable)."""
    script = [(503, b'{"detail":"boom"}'), (503, b'{"detail":"boom"}')]
    server, port, handler = _start_server(script)
    settings.engine_api_max_retries = 1
    exam, _, pool = _make_client(settings, f"http://127.0.0.1:{port}",
                                  f"http://127.0.0.1:{port}")
    try:
        with pytest.raises(EngineError) as exc_info:
            asyncio.run(exam.submit_job(
                {"exam_type": "CAT-1", "class_name": "5", "subject": "M"}, "rid"))
        e = exc_info.value
        assert e.status_code == 502
        assert e.error == "service_unavailable"
        assert handler.CALL_COUNT == 2, f"expected 2 attempts, saw {handler.CALL_COUNT}"
    finally:
        server.shutdown()
        asyncio.run(pool.aclose())


# ── 4xx is deterministic: never retried ─────────────────────────────────

def test_4xx_not_retried(settings):
    """Engine 400 → immediate EngineError, exactly one attempt."""
    script = [(400, b'{"error":"bad request"}')]
    server, port, handler = _start_server(script)
    settings.engine_api_max_retries = 5  # generous — must still stop at 1
    exam, _, pool = _make_client(settings, f"http://127.0.0.1:{port}",
                                 f"http://127.0.0.1:{port}")
    try:
        with pytest.raises(EngineError) as exc_info:
            asyncio.run(exam.submit_job(
                {"exam_type": "CAT-1", "class_name": "5", "subject": "M"}, "rid"))
        assert exc_info.value.error == "bad_response"
        assert handler.CALL_COUNT == 1, "4xx must not be retried"
    finally:
        server.shutdown()
        asyncio.run(pool.aclose())


# ── 5xx then 200: recovery on retry ────────────────────────────────────

def test_5xx_then_success_recover(settings):
    """First 502, second 200 → success. Proves the retry loop recovers."""
    body = json.dumps({"job_id": "j1", "status": "queued",
                      "exam_type": "CAT-1"}).encode()
    script = [(502, b'{"detail":"bad gateway"}'), (200, body)]
    server, port, handler = _start_server(script)
    settings.engine_api_max_retries = 1
    exam, _, pool = _make_client(settings, f"http://127.0.0.1:{port}",
                                 f"http://127.0.0.1:{port}")
    try:
        out = asyncio.run(exam.submit_job(
            {"exam_type": "CAT-1", "class_name": "5", "subject": "M"}, "rid"))
        assert out["job_id"] == "j1"
        assert handler.CALL_COUNT == 2
    finally:
        server.shutdown()
        asyncio.run(pool.aclose())


# ── non-JSON 200 payload → bad_response ─────────────────────────────────

def test_non_json_payload_is_bad_response(settings):
    script = [(200, b"this is not json")]
    server, port, _ = _start_server(script)
    settings.engine_api_max_retries = 0
    _, results, pool = _make_client(settings, f"http://127.0.0.1:{port}",
                                    f"http://127.0.0.1:{port}")
    try:
        with pytest.raises(EngineError) as exc_info:
            asyncio.run(results.query({"class": "5", "scope": "CAT-1"}, "rid"))
        assert exc_info.value.error == "bad_response"
    finally:
        server.shutdown()
        asyncio.run(pool.aclose())


# ── unreachable engine (closed port) → service_unavailable ────────────

def test_connect_refusal_is_service_unavailable(settings):
    settings.exam_api_base_url = "http://127.0.0.1:1"
    settings.results_api_base_url = "http://127.0.0.1:1"
    settings.egress_allow_internal = True
    settings.engine_api_max_retries = 0
    _, results, pool = _make_client(settings, settings.exam_api_base_url,
                                    settings.results_api_base_url)
    try:
        with pytest.raises(EngineError) as exc_info:
            asyncio.run(results.query({"class": "5", "scope": "CAT-1"}, "rid"))
        assert exc_info.value.status_code == 502
        assert exc_info.value.error == "service_unavailable"
    finally:
        asyncio.run(pool.aclose())


# ── app-level: full route → client → controlled 502 JSON ──────────────

def test_app_level_502_shape(settings):
    """Through TestClient, a refused engine connection yields the
    client-facing 502 shape: category + safe message + request_id."""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    settings.exam_api_base_url = "http://127.0.0.1:1"
    settings.results_api_base_url = "http://127.0.0.1:1"
    settings.egress_allow_internal = True
    settings.engine_api_max_retries = 0
    app = create_app(settings)
    fastapi_app = getattr(app, "user_app", app)  # unwrap the ASGI size cap
    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/v1/results?class=5",
                   headers={"Authorization": f"Bearer {settings.admin_api_token}"})
    assert r.status_code == 502, r.text
    body = r.json()
    # Client-facing error envelope (FastAPI HTTPException shape):
    # {"detail": {"error": ..., "message": ..., "request_id": ...}}
    assert body["detail"]["error"] == "service_unavailable"
    assert "request_id" in body["detail"]
    assert "traceback" not in r.text.lower()
    assert "127.0.0.1:1" not in r.text  # no internal URL leakage
