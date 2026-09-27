"""Engine integration tests — run against the stub doubles.

They assert the platform's behaviour at the boundary: typed requests go
out, typed responses come back, and result figures pass through untouched.
"""

from __future__ import annotations


def test_exam_job_submit_and_status(client, auth_headers):
    r = client.post("/api/v1/examinations/jobs", headers=auth_headers, json={
        "exam_type": "CAT-1", "class_name": "5", "subject": "Mathematics",
    })
    assert r.status_code == 200
    job = r.json()
    assert job["exam_type"] == "CAT-1"
    assert job["status"] in ("queued", "running", "succeeded", "failed")
    assert job["job_id"]

    r2 = client.get(f"/api/v1/examinations/jobs/{job['job_id']}", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["job_id"] == job["job_id"]


def test_exam_job_rejects_unknown_type(client, auth_headers):
    r = client.post("/api/v1/examinations/jobs", headers=auth_headers, json={
        "exam_type": "MIDTERM", "class_name": "5", "subject": "Math",
    })
    assert r.status_code == 422


def test_exam_types_listed(client):
    assert client.get("/api/v1/examinations/types").json() == ["CAT-1", "CAT-2", "FINAL"]


def test_results_passthrough_untouched(client, auth_headers):
    r = client.get("/api/v1/results?scope=CAT-1&class=5", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["items"], "stub should return at least one row"
    row = body["items"][0]
    # The platform must not recompute: values arrive exactly as the engine sent.
    assert row["marks_obtained"] == 20.0
    assert row["total_marks"] == 25.0
    assert row["percentage"] == 80.0
    assert row["grade"] == "A"
    assert row["result"] == "PASS"


def test_results_rejects_bad_scope(client, auth_headers):
    r = client.get("/api/v1/results?scope=QUIZ&class=5", headers=auth_headers)
    assert r.status_code == 422


def test_results_require_auth(client):
    assert client.get("/api/v1/results?scope=CAT-1&class=5").status_code == 401


def test_engine_unreachable_is_502(client, auth_headers, settings):
    settings.exam_api_base_url = "http://127.0.0.1:1"  # nothing listening
    r = client.post("/api/v1/examinations/jobs", headers=auth_headers, json={
        "exam_type": "CAT-1", "class_name": "5", "subject": "Math",
    })
    assert r.status_code == 502
    assert "Exam Engine" in r.json()["detail"]
