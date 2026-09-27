"""Integration-hardening tests: guards, history, dedupe, AI contexts,
request size limits, and the real-data dashboard contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.gateway import AIGateway
from app.ai.prompts import allowed_contexts, build_system_prompt
from app.config import Settings


def _settings(**over) -> Settings:
    base = {
        "app_env": "development",
        "app_secret_key": "test-secret-key-0123456789abcdef",
        "admin_api_token": "test-admin-token-0123456789abcdef",
        "database_path": "/tmp/sap-unused.db",
        "exam_api_base_url": "stub://exam-engine",
        "results_api_base_url": "stub://results-engine",
        "ai_use_stub": True,
    }
    base.update(over)
    return Settings(**base)


# ── config guards ──────────────────────────────────────────────────────

def test_production_rejects_stub_engine_urls():
    with pytest.raises(ValidationError):
        _settings(app_env="production",
                  exam_api_base_url="stub://exam-engine",
                  results_api_base_url="http://engines.local/results")


def test_production_rejects_ai_stub():
    with pytest.raises(ValidationError):
        _settings(app_env="production",
                  exam_api_base_url="http://engines.local/exam",
                  results_api_base_url="http://engines.local/results",
                  ai_use_stub=True)


def test_production_accepts_real_urls():
    s = _settings(app_env="production",
                  exam_api_base_url="http://engines.local/exam",
                  results_api_base_url="https://engines.local/results",
                  ai_use_stub=False)
    assert not s.stub_mode


@pytest.mark.parametrize("bad", [
    "ftp://engines.local/exam",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http://user@evil.local/exam",
])
def test_unsafe_service_urls_rejected(bad):
    with pytest.raises(ValidationError):
        _settings(exam_api_base_url=bad)


def test_stub_mode_flag_reflects_any_stub():
    assert _settings().stub_mode
    assert not _settings(
        exam_api_base_url="http://a.local", results_api_base_url="http://b.local",
        ai_use_stub=False).stub_mode


# ── engine health probes ───────────────────────────────────────────────

def test_ready_reports_stub_engines(client):
    ready = client.get("/api/v1/ready").json()
    assert ready["stub_mode"] is True
    assert ready["engines"]["exam"]["status"] == "stub"
    assert ready["engines"]["results"]["status"] == "stub"
    assert ready["ai"]["stub_mode"] is True
    assert ready["ai"]["providers_configured"] == 1  # stub provider


def test_engine_health_unreachable_never_raises(settings):
    from app.services.engine_client import ExamEngineClient
    s = Settings(app_env="development",
                 app_secret_key=settings.app_secret_key,
                 admin_api_token=settings.admin_api_token,
                 database_path=str(settings.database_path),
                 exam_api_base_url="http://127.0.0.1:1",  # closed port
                 results_api_base_url="stub://results-engine",
                 ai_use_stub=True)
    h = ExamEngineClient(s).health(request_id="t")
    assert h["status"] == "unreachable"
    assert h["engine"] == "exam-engine"


def test_engine_health_ok_path_reports_ok(settings):
    """The reachable-engine path must report ok/degraded, never raise."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.services.engine_client import ExamEngineClient

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        s = Settings(app_env="development",
                     app_secret_key=settings.app_secret_key,
                     admin_api_token=settings.admin_api_token,
                     database_path=str(settings.database_path),
                     exam_api_base_url=f"http://127.0.0.1:{port}",
                     results_api_base_url="stub://results-engine",
                     ai_use_stub=True)
        h = ExamEngineClient(s).health(request_id="t")
    finally:
        server.shutdown()
    assert h["status"] == "ok"
    assert h["engine"] == "exam-engine"


# ── history + activity + counts ─────────────────────────────────────────

def test_exam_job_submission_recorded_in_history(client, auth_headers):
    job = client.post("/api/v1/examinations/jobs", headers=auth_headers, json={
        "exam_type": "CAT-1", "class_name": "5", "subject": "Mathematics"}).json()
    hist = client.get("/api/v1/examinations/history", headers=auth_headers).json()
    assert any(h["job_id"] == job["job_id"] and h["exam_type"] == "CAT-1"
               and h["class_name"] == "5" for h in hist)


def test_exam_history_requires_auth(client):
    assert client.get("/api/v1/examinations/history").status_code == 401


def test_result_query_recorded_in_history(client, auth_headers):
    data = client.get("/api/v1/results?class=5", headers=auth_headers).json()
    hist = client.get("/api/v1/results/history", headers=auth_headers).json()
    assert any(h["scope"] == "CAT-1" and h["class_name"] == "5"
               and h["item_count"] == len(data["items"]) for h in hist)


def test_activity_feed_has_no_pii(client, auth_headers, sample_student):
    client.post("/api/v1/students", headers=auth_headers, json=sample_student)
    act = client.get("/api/v1/activity", headers=auth_headers).json()
    assert act, "expected audit activity after student creation"
    for entry in act:
        joined = " ".join(str(v) for v in entry.values())
        assert "03001234567" not in joined
        assert "Ali Raza" not in joined
        assert set(entry) <= {"ts", "actor", "action", "entity", "entity_id"}


def test_student_counts_real_data(client, auth_headers, sample_student):
    r1 = client.post("/api/v1/students", headers=auth_headers, json=sample_student).json()
    s2 = dict(sample_student, full_name="Sara Khan", father_name="Ahmed Khan")
    client.post("/api/v1/students", headers=auth_headers, json=s2)
    client.patch(f"/api/v1/students/{r1['id']}",
                 headers=auth_headers, json={"status": "inactive"})
    counts = client.get("/api/v1/ready").json()["students"]
    assert counts == {"total": 2, "active": 1, "inactive": 1}


# ── import dedupe ───────────────────────────────────────────────────────

def test_import_retry_does_not_duplicate(client, auth_headers):
    rows = [
        {"full_name": "Ali Raza", "father_name": "Muhammad Raza", "class_name": "5"},
        {"full_name": "Sara Khan", "father_name": "Ahmed Khan", "class_name": "5"},
    ]
    first = client.post("/api/v1/students/import-rows", headers=auth_headers,
                        json=rows).json()
    second = client.post("/api/v1/students/import-rows", headers=auth_headers,
                         json=rows).json()
    assert first["created"] == 2
    assert second["created"] == 0
    assert second["duplicates"] == 2
    total = client.get("/api/v1/ready").json()["students"]["total"]
    assert total == 2


# ── AI contexts ─────────────────────────────────────────────────────────

def test_all_task_context_combinations_routed():
    assert build_system_prompt("summarize_results", "RESULTS")
    assert build_system_prompt("parent_message", "GENERAL")
    assert "sole source of truth" in build_system_prompt("summarize_results", "RESULTS")
    assert "general school-platform assistance" in build_system_prompt("parent_message", "GENERAL")


def test_disallowed_context_combination_rejected():
    with pytest.raises(ValueError):
        build_system_prompt("draft_exam_outline", "GENERAL")  # no exam data context
    with pytest.raises(ValueError):
        build_system_prompt("draft_exam_outline", "RESULTS")


def test_unknown_context_rejected():
    with pytest.raises(ValueError):
        build_system_prompt("parent_message", "MIDTERM")


def test_contexts_listing_per_task(client):
    ctxs = client.get("/api/v1/ai/contexts?task=draft_exam_outline").json()
    assert set(ctxs) == {"CAT-1", "CAT-2", "FINAL"}
    all_ctxs = client.get("/api/v1/ai/contexts").json()
    assert set(all_ctxs) == {"CAT-1", "CAT-2", "FINAL", "RESULTS", "GENERAL"}
    assert set(allowed_contexts("parent_message")) == set(all_ctxs)


def test_gateway_run_with_results_context(settings):
    gw = AIGateway(settings)
    res = gw.run(task="summarize_results", context="RESULTS",
                 user_input="Rows: APS-1: obtained=20, total=25, grade=A, result=PASS",
                 request_id="ctx1")
    assert res.ok


def test_gateway_run_invalid_context_never_calls_provider(settings):
    gw = AIGateway(settings)
    with pytest.raises(ValueError):
        gw.run(task="draft_exam_outline", context="GENERAL",
               user_input="x", request_id="ctx2")


# ── request size gate ───────────────────────────────────────────────────

def test_oversized_body_rejected_413(client):
    big = "x" * 1024
    res = client.post("/api/v1/students", content=big,
                      headers={"Content-Type": "application/json",
                               "Content-Length": str(20 * 1024 * 1024)})
    assert res.status_code == 413


# ── end-to-end flows ────────────────────────────────────────────────────

def test_e2e_student_flow(client, auth_headers, sample_student):
    created = client.post("/api/v1/students", headers=auth_headers,
                          json=sample_student).json()
    assert created["student_code"].startswith("APS-")
    assert created["status"] == "active"
    assert client.get(f"/api/v1/students/{created['id']}",
                      headers=auth_headers).json()["full_name"] == "Ali Raza"
    assert client.get("/api/v1/students?q=Ali", headers=auth_headers).json()["total"] == 1
    upd = client.patch(f"/api/v1/students/{created['id']}", headers=auth_headers,
                       json={"section": "B"}).json()
    assert upd["section"] == "B"
    assert upd["student_code"] == created["student_code"]  # ID never changes
    deact = client.patch(f"/api/v1/students/{created['id']}",
                         headers=auth_headers, json={"status": "inactive"}).json()
    assert deact["status"] == "inactive"
    assert client.delete(f"/api/v1/students/{created['id']}",
                         headers=auth_headers).status_code == 405  # no hard delete
    act = client.get("/api/v1/activity", headers=auth_headers).json()
    assert any(a["entity_id"] == created["student_code"] for a in act)


def test_e2e_exam_flows_all_types(client, auth_headers):
    for exam_type in ("CAT-1", "CAT-2", "FINAL"):
        job = client.post("/api/v1/examinations/jobs", headers=auth_headers, json={
            "exam_type": exam_type, "class_name": "6", "subject": "Science"}).json()
        assert job["exam_type"] == exam_type
        status = client.get(f"/api/v1/examinations/jobs/{job['job_id']}",
                            headers=auth_headers).json()
        assert status["job_id"] == job["job_id"]
    hist = client.get("/api/v1/examinations/history", headers=auth_headers).json()
    assert len(hist) >= 3


def test_e2e_results_flows_all_scopes(client, auth_headers):
    for scope in ("CAT-1", "CAT-2", "FINAL", "CONSOLIDATED"):
        data = client.get(f"/api/v1/results?class=7&scope={scope}",
                          headers=auth_headers).json()
        assert "items" in data and "source" in data
        for row in data["items"]:
            assert {"student_code", "marks_obtained", "grade", "result"} <= set(row)
    hist = client.get("/api/v1/results/history", headers=auth_headers).json()
    assert len(hist) >= 4


def test_e2e_ai_flow_stub(client, auth_headers):
    res = client.post("/api/v1/ai/run", headers=auth_headers, json={
        "task": "draft_exam_outline", "context": "CAT-2",
        "input": "Outline for class 4 English, recent topics."}).json()
    assert res["ok"] and res["provider"] == "stub"
    assert "sections" in res["data"]
