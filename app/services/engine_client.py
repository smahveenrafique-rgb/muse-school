"""Shared HTTP client for the upstream engines.

- Base URLs come from the environment; nothing is hard-coded.
- Every request carries X-Request-ID for correlation across the platform
  and the engines.
- Retry policy: safe (idempotent) GETs are retried; POSTs carry an
  Idempotency-Key and are retried only while no response was received.
- `stub://` base URLs select the deterministic dev/test double — canned
  payloads shaped like the real contracts, zero business logic.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.logging_setup import get_logger, log_event

log = get_logger("engines")


class EngineError(Exception):
    def __init__(self, message: str, *, status_code: int = 502,
                 engine: str = "engine", detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.engine = engine
        self.detail = detail


class EngineClient:
    def __init__(self, settings: Settings, base_url: str, engine_name: str):
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.engine_name = engine_name
        self.stub = base_url.startswith("stub://")

    def _client(self) -> httpx.Client:
        # trust_env=False: engine traffic must never inherit the operator's
        # shell proxy variables — routing is fully determined by config.
        return httpx.Client(
            base_url=self.base_url if not self.stub else "http://stub.local",
            timeout=self.settings.engine_api_timeout_seconds,
            trust_env=False,
        )

    def _request(self, method: str, path: str, request_id: str,
                 json: dict | None = None, params: dict | None = None) -> Any:
        if self.stub:
            return self._stub_response(method, path, json=json, params=params)
        headers = {
            "X-Request-ID": request_id,
            "Accept": "application/json",
        }
        idempotency_key = ""
        if method.upper() == "POST":
            idempotency_key = uuid.uuid4().hex
            headers["Idempotency-Key"] = idempotency_key

        attempts = 1 + self.settings.engine_api_max_retries
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                with self._client() as client:
                    resp = client.request(method, path, headers=headers,
                                          json=json, params=params)
                latency_ms = (time.perf_counter() - t0) * 1000
                log_event(log, 20, "engine response",
                          engine=self.engine_name, method=method, path=path,
                          status=resp.status_code, attempt=attempt,
                          latency_ms=round(latency_ms, 1))
                if resp.status_code >= 500 and attempt < attempts and method.upper() == "GET":
                    time.sleep(0.5 * attempt)
                    continue
                self._raise_for_bad_status(resp)
                return resp.json()
            except EngineError:
                raise
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                log_event(log, 30, "engine request failed",
                          engine=self.engine_name, method=method, path=path,
                          attempt=attempt, error=type(exc).__name__)
                if attempt < attempts:
                    time.sleep(0.5 * attempt)
            except httpx.HTTPError as exc:
                raise EngineError(str(exc), engine=self.engine_name) from exc
        raise EngineError(f"{self.engine_name} unreachable after {attempts} attempts",
                          engine=self.engine_name,
                          detail=str(last_exc)[:300] if last_exc else "")

    # ── stub double ──────────────────────────────────────────────────────
    # Deterministic canned payloads matching docs/API.md. Used for local
    # development and CI when the real engines are not running. Contains no
    # exam/result business logic — values are fixed literals.

    def _raise_for_bad_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise EngineError(
                f"{self.engine_name} returned {resp.status_code}",
                status_code=502, engine=self.engine_name,
                detail=resp.text[:500])

    def _stub_response(self, method: str, path: str, json: dict | None,
                       params: dict | None) -> Any:
        log_event(log, 20, "engine stub response",
                  engine=self.engine_name, method=method, path=path)
        is_exam = self.engine_name == "exam-engine"
        if is_exam and method == "POST" and path == "/api/v1/exam-jobs":
            body = json or {}
            return {
                "job_id": f"stub-job-{uuid.uuid4().hex[:8]}",
                "status": "queued",
                "exam_type": body.get("exam_type", "CAT-1"),
                "detail": "stub: accepted (no real engine running)",
                "artifact_url": "",
            }
        if is_exam and method == "GET" and path.startswith("/api/v1/exam-jobs/"):
            return {
                "job_id": path.rsplit("/", 1)[-1],
                "status": "succeeded",
                "exam_type": "CAT-1",
                "detail": "stub: completed (no real engine running)",
                "artifact_url": "",
            }
        is_results = self.engine_name == "results-engine"
        if is_results and method == "GET" and path == "/api/v1/results":
            p = params or {}
            return {
                "items": [
                    {
                        "student_code": p.get("student_code", "APS-2026-0001"),
                        "student_name": "Stub Student",
                        "class_name": p.get("class_name", ""),
                        "subject": p.get("subject", ""),
                        "exam_type": p.get("scope", "CAT-1"),
                        "marks_obtained": 20.0,
                        "total_marks": 25.0,
                        "percentage": 80.0,
                        "grade": "A",
                        "result": "PASS",
                    }
                ],
                "generated_at": "2026-01-01T00:00:00Z",
                "source": "stub-results-engine",
            }
        raise EngineError(f"stub has no canned response for {method} {path}",
                          engine=self.engine_name)

    def health(self, request_id: str = "health") -> dict:
        """Engine health probe for the platform dashboard / Settings page.

        Contract expectation (documented in docs/API.md):
        {base_url}/api/v1/health -> {"status": "ok"}.
        Stub mode returns {"status": "stub"} so a stub can never be mistaken
        for a live engine. Unreachable engines return "unreachable" — never
        raise, so the dashboard can render a controlled "Not Connected" card.
        """
        start = time.perf_counter()
        if self.stub:
            return {"status": "stub", "engine": self.engine_name,
                    "latency_ms": 0,
                    "detail": "explicit stub mode (dev/test only — not production)"}
        try:
            with self._client() as client:
                raw = client.get("/api/v1/health",
                                 headers={"X-Request-ID": request_id,
                                          "Accept": "application/json"})
        except httpx.HTTPError as exc:
            return {"status": "unreachable", "engine": self.engine_name,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "detail": str(exc)[:300]}
        latency = int((time.perf_counter() - start) * 1000)
        if 200 <= raw.status_code < 300:
            return {"status": "ok", "engine": self.engine_name,
                    "latency_ms": latency, "detail": f"HTTP {raw.status_code}"}
        return {"status": "degraded", "engine": self.engine_name,
                "latency_ms": latency, "detail": f"HTTP {raw.status_code}"}


class ExamEngineClient(EngineClient):
    def __init__(self, settings: Settings):
        super().__init__(settings, settings.exam_api_base_url, "exam-engine")

    def submit_job(self, payload: dict, request_id: str) -> dict:
        return self._request("POST", "/api/v1/exam-jobs", request_id, json=payload)

    def get_job(self, job_id: str, request_id: str) -> dict:
        return self._request("GET", f"/api/v1/exam-jobs/{job_id}", request_id)


class ResultsEngineClient(EngineClient):
    def __init__(self, settings: Settings):
        super().__init__(settings, settings.results_api_base_url, "results-engine")

    def query(self, params: dict, request_id: str) -> dict:
        return self._request("GET", "/api/v1/results", request_id, params=params)
