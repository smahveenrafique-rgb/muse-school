"""Shared HTTP client for the upstream engines.

- Base URLs come from the environment; nothing is hard-coded.
- Every request carries X-Request-ID for correlation across the platform
  and the engines.
- Retry policy: safe (idempotent) GETs and transient 5xx / transport
  failures are retried with bounded exponential backoff + jitter; POSTs
  carry an Idempotency-Key so a retry is a duplicate, not a new
  submission. Deterministic client errors (4xx) are never retried.
- `stub://` base URLs select the deterministic dev/test double — canned
  payloads shaped like the real contracts, zero business logic.

Concurrency: one shared `httpx.AsyncClient` pool (connection reuse) is
attached via `set_pool()`. Each engine gets a base_url-bound view over
that pool, so exam and results traffic share the connection pool while
keeping distinct base URLs. A private client is created only when no
shared pool is attached (standalone scripts/tests).
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.logging_setup import get_logger, log_event

log = get_logger("engines")

_POOL_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=50)


class EngineError(Exception):
    """Controlled engine failure. `error` is a machine-readable category.

    `detail` is internal-only (safe to log, never returned to clients).
    """

    def __init__(self, message: str, *, status_code: int = 502,
                 engine: str = "engine", detail: str = "",
                 error: str = "service_unavailable"):
        super().__init__(message)
        self.status_code = status_code
        self.engine = engine
        self.detail = detail
        self.error = error


class EngineClient:
    def __init__(self, settings: Settings, base_url: str, engine_name: str):
        self.settings = settings
        self.base_url = base_url.rstrip("/")
        self.engine_name = engine_name
        self.stub = base_url.startswith("stub://")
        self._pool: httpx.AsyncClient | None = None
        self._owns_pool = False
        self._last_transient: Exception | None = None

    def set_pool(self, client: httpx.AsyncClient) -> None:
        """Attach the app-level shared async pool (connection reuse).

        The pool is transport-level; the engine's base URL is applied per
        request via a thin base_url-bound view, so a single pool serves
        both the exam and results engines.
        """
        self._pool = client
        self._owns_pool = False

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.settings.engine_api_timeout_seconds,
                             connect=5.0, pool=5.0)

    def _client(self) -> httpx.AsyncClient:
        """Return the transport used for this engine.

        With a shared app-level pool (no base URL), requests are issued
        with absolute URLs built from `self.base_url` + path (see
        `_request_url`). Without a pool, a private base_url-bound client
        is created and owned by this instance.
        """
        if self._pool is not None and not self._owns_pool:
            return self._pool
        if self._pool is None:
            self._pool = httpx.AsyncClient(
                base_url=self.base_url if not self.stub else "http://stub.local",
                timeout=self._timeout(),
                trust_env=False,
                limits=_POOL_LIMITS,
            )
            self._owns_pool = True
        return self._pool

    def _request_url(self, path: str) -> str:
        """Absolute URL for this engine, shared-pool safe."""
        if self._pool is not None and not self._owns_pool:
            return f"{self.base_url}{path}"
        return path

    async def _request(self, method: str, path: str, request_id: str,
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

        client = self._client()
        url = self._request_url(path)
        attempts = 1 + self.settings.engine_api_max_retries
        base_delay = 0.25
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            resp = await self._send_once(client, method, url, headers, json,
                                          params)
            if resp is None:
                # Transient failure (timeout / connect error): log, back off,
                # and retry unless this was the final attempt.
                self._log_transient(method, path, attempt)
                if attempt < attempts:
                    await asyncio.sleep(base_delay * (2 ** (attempt - 1))
                                        + random.uniform(0, 0.1))
                continue
            latency_ms = (time.perf_counter() - t0) * 1000
            log_event(log, 20, "engine response",
                      engine=self.engine_name, method=method, path=path,
                      status=resp.status_code, attempt=attempt,
                      latency_ms=round(latency_ms, 1))
            if resp.status_code >= 500 and attempt < attempts:
                # Transient server failure: retry with backoff. 4xx is
                # deterministic (bad request / auth) and never retried.
                await asyncio.sleep(base_delay * (2 ** (attempt - 1))
                                    + random.uniform(0, 0.1))
                continue
            self._raise_for_bad_status(resp)
            try:
                return resp.json()
            except ValueError as exc:
                raise EngineError(f"{self.engine_name} returned non-JSON payload",
                                  engine=self.engine_name,
                                  detail=str(exc)[:200],
                                  error="bad_response") from exc
        raise EngineError(f"{self.engine_name} unreachable after {attempts} attempts",
                          engine=self.engine_name,
                          detail=str(self._last_transient)[:300]
                          if self._last_transient else "",
                          error="service_unavailable")

    def _log_transient(self, method: str, path: str, attempt: int) -> None:
        transient = self._last_transient
        log_event(log, 30, "engine request failed",
                  engine=self.engine_name, method=method, path=path,
                  attempt=attempt,
                  error=type(transient).__name__ if transient else "unknown")

    async def _send_once(self, client: httpx.AsyncClient, method: str,
                         url: str, headers: dict, json: dict | None,
                         params: dict | None) -> httpx.Response | None:
        """One HTTP attempt. Returns the response, or `None` after a
        transient transport failure (recorded for the retry loop)."""
        try:
            return await client.request(method, url, headers=headers,
                                        json=json, params=params)
        except (httpx.TimeoutException, httpx.ConnectError,
                httpx.ReadError, httpx.WriteError,
                httpx.PoolTimeout, httpx.NetworkError) as exc:
            self._last_transient = exc
            return None
        except httpx.HTTPError as exc:
            raise EngineError(f"{self.engine_name} request error",
                              engine=self.engine_name,
                              detail=str(exc)[:300],
                              error="service_unavailable") from exc

    # ── stub double ──────────────────────────────────────────────────────
    # Deterministic canned payloads matching docs/API.md. Used for local
    # development and CI when the real engines are not running. Contains no
    # exam/result business logic — values are fixed literals.

    def _raise_for_bad_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            # 5xx => service_unavailable (transient, retried above);
            # 4xx => deterministic bad_response (never retried).
            # `detail` is internal-only: the handler logs it and returns a
            # generic client-facing message — no upstream payload leakage.
            category = ("service_unavailable" if resp.status_code >= 500
                        else "bad_response")
            raise EngineError(
                f"{self.engine_name} returned {resp.status_code}",
                status_code=502,
                engine=self.engine_name,
                detail=resp.text[:500],
                error=category)

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
                          engine=self.engine_name,
                          error="bad_response")

    async def health(self, request_id: str = "health") -> dict:
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
            client = self._client()
            raw = await client.get(
                self._request_url("/api/v1/health"),
                headers={"X-Request-ID": request_id,
                         "Accept": "application/json"})
        except httpx.HTTPError as exc:
            return {"status": "unreachable", "engine": self.engine_name,
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "detail": f"engine unreachable: {type(exc).__name__}"}
        latency = int((time.perf_counter() - start) * 1000)
        if 200 <= raw.status_code < 300:
            return {"status": "ok", "engine": self.engine_name,
                    "latency_ms": latency, "detail": f"HTTP {raw.status_code}"}
        return {"status": "degraded", "engine": self.engine_name,
                "latency_ms": latency, "detail": f"HTTP {raw.status_code}"}

    async def aclose(self) -> None:
        """Close the private pool (no-op when a shared pool is attached —
        the app owns that pool's lifetime via the shutdown hook)."""
        if self._owns_pool and self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.aclose()


class ExamEngineClient(EngineClient):
    def __init__(self, settings: Settings):
        super().__init__(settings, settings.exam_api_base_url, "exam-engine")

    async def submit_job(self, payload: dict, request_id: str) -> dict:
        return await self._request("POST", "/api/v1/exam-jobs", request_id, json=payload)

    async def get_job(self, job_id: str, request_id: str) -> dict:
        return await self._request("GET", f"/api/v1/exam-jobs/{job_id}", request_id)


class ResultsEngineClient(EngineClient):
    def __init__(self, settings: Settings):
        super().__init__(settings, settings.results_api_base_url, "results-engine")

    async def query(self, params: dict, request_id: str) -> dict:
        return await self._request("GET", "/api/v1/results", request_id, params=params)
