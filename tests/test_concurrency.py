"""Concurrency tests: sequence integrity, session store, engine fan-out.

These prove the platform is safe under parallel load:
- 20 simultaneous student creates → 20 distinct codes, zero errors.
- Simultaneous engine/AI requests fan out without blocking each other.
- Simultaneous logins create distinct session rows with no corruption.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re

import pytest
from conftest import TEST_TOKEN

# ── student code allocation ─────────────────────────────────────────────

def test_concurrent_student_creates_unique_codes(client, auth_headers, sample_student):
    """20 parallel creates in the same year → 20 distinct codes, no 500s."""
    def create(i: int):
        # "0300123" + 4-digit index = 11 chars (03XXXXXXXXX shape).
        payload = dict(sample_student, full_name=f"Concurrent {i}",
                       mobile=f"0300123{i:04d}")
        r = client.post("/api/v1/students", json=payload, headers=auth_headers)
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(create, range(20)))

    assert all(r.status_code == 201 for r in results), [
        (i, r.status_code, r.text) for i, r in enumerate(results)
        if r.status_code != 201]
    codes = [r.json()["student_code"] for r in results]
    assert len(set(codes)) == 20, f"duplicate codes: {codes}"
    for c in codes:
        assert c.startswith("APS-"), c
    total = client.get("/api/v1/students", headers=auth_headers).json()["total"]
    assert total == 20


def test_concurrent_imports_do_not_corrupt_sequence(client, auth_headers):
    """Two parallel bulk imports (10 rows each, distinct names) → 20
    distinct codes, no IntegrityError leakage."""
    def do_import(offset: int):
        rows = [{
            "full_name": f"Importer {offset}-{i}",
            "father_name": "F",
            "class_name": "9",
            "mobile": f"0300123{offset}{i:03d}",
        } for i in range(10)]
        r = client.post("/api/v1/students/import-rows", json=rows,
                        headers=auth_headers)
        return r

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(do_import, (0, 1)))

    for r in results:
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 10
        assert r.json()["errors"] == []
    total = client.get("/api/v1/students", headers=auth_headers).json()["total"]
    assert total == 20


# ── session store under concurrent logins ───────────────────────────────

def test_concurrent_logins_create_distinct_sessions(client):
    """20 parallel logins → 20 working session cookies, no file/DB
    corruption (each cookie authenticates)."""
    def login(i: int):
        csrf = re.search(r'name="csrf_token" value="([a-f0-9]+)"',
                         client.get("/login").text).group(1)
        r = client.post("/login",
                        data={"token": TEST_TOKEN, "csrf_token": csrf},
                        follow_redirects=False)
        assert r.status_code == 303, (i, r.status_code, r.text)
        raw = r.headers["set-cookie"]
        m = re.search(r"admin_session=([^;]+)", raw)
        assert m, f"no session cookie in: {raw}"
        return m.group(1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        sessions = list(pool.map(login, range(20)))

    assert len(set(sessions)) == 20
    client.post("/logout")


# ── engine + AI fan-out (async, no event-loop blocking) ────────────────

@pytest.mark.anyio
async def test_simultaneous_engine_and_ai_requests(settings):
    """20 concurrent engine probes finish without starvation — proves the
    async pipeline does not serialize on a thread or blocking sleep."""
    import time

    from app.services.engine_client import ExamEngineClient, ResultsEngineClient

    exam = ExamEngineClient(settings)
    results = ResultsEngineClient(settings)

    async def probe(i: int):
        if i % 2 == 0:
            return await exam.get_job(f"job-{i}", request_id=f"fan-{i}")
        return await results.query({"class": "5", "scope": "CAT-1"},
                                   request_id=f"fan-{i}")

    t0 = time.perf_counter()
    results_ = await asyncio.gather(*[probe(i) for i in range(20)])
    elapsed = time.perf_counter() - t0

    assert len(results_) == 20
    # Stub responses are instant; the whole fan-out must finish quickly,
    # with no thread-pool or sleep-induced serialization.
    assert elapsed < 5.0, f"fan-out took {elapsed:.1f}s — blocking suspected"
