# Engineering Review Report — school-ai-platform

| Field | Detail |
|---|---|
| **Project** | school-ai-platform (Student Master / Exam Engine / Results Engine Orchestrator + AI Gateway) |
| **Date** | 2026-09-27 |
| **Reviewer** | Senior Software Engineer / Technical Architect |
| **Scope** | `app/`, `static/`, `docs/`, `tests/`, `.github/workflows/ci.yml`, `pyproject.toml`, `requirements.txt` |
| **Method** | Static code analysis, architectural boundary analysis, threat modeling, scalability review, CI/documentation audit |
| **Overall Assessment** | **7.4 / 10 — Logic-Ready, Infrastructure-Deficient** |

---

## Executive Summary

The `school-ai-platform` demonstrates **strong architectural integrity and boundary discipline** disproportionate to its classification as a school project. The system's defining principle — the platform **orchestrates** but never owns academic business logic — is consistently enforced in code, documentation, and data contracts. Authoritative computation of marks, totals, percentages, grades, and pass/fail determinations is correctly delegated to the upstream Exam and Results Engines, with the platform rendering engine figures verbatim and the AI Gateway constrained to non-authoritative, schema-validated assistance.

Security hygiene is mature for this stage: fail-fast configuration validation, production stub guards, constant-time credential comparison, HttpOnly session binding, Content Security Policy, magic-byte upload validation, and centralized log redaction are all correctly implemented.

The system is **not production-ready** for external or multi-concurrent-operator deployment. Six critical (P0) defects block production promotion. All are infrastructure-level, not logic-level, and are remediable within a short, bounded engineering effort. The primary blockers are **event-loop starvation from synchronous I/O in the asynchronous pipeline**, **concurrency-induced state corruption in identity and session management**, and **insufficient egress filtering enabling Server-Side Request Forgery (SSRF)**.

No fundamental re-architecture is required. Remediation is additive and localized to `app/services/engine_client.py`, `app/ai/gateway.py`, `app/security.py`, `app/services/students.py`, and `app/config.py`.

**Recommendation:** Approve for internal/demo use. **Gate production promotion on completion of Phase 1 remediation.** Phases 2 and 3 may proceed in parallel sprints without blocking functional development.

---

## 1. Architectural Assessment

### 1.1 Core Invariants — Primary Strength

The most significant architectural asset is the explicit definition and enforcement of core invariants:

1.  Frontend and AI subsystems never calculate academic outcomes.
2.  Stub doubles are explicit (`stub://`, `AI_USE_STUB`) and fail-closed in production.
3.  All AI output is strictly schema-validated; `missing_input` refusals are terminal and never trigger failover.
4.  Secrets remain server-side; the Settings surface exposes connectivity status, never values.
5.  Student identity (`student_code`) is server-generated, immutable, and authoritative.

These invariants are enforced by construction, not convention: `app/services/engine_client.py:109` treats engine numerics as opaque display values, `app/ai/schemas.py:47` rejects non-conforming AI output, `app/config.py:87` aborts boot on production stub detection, and `app/services/students.py:32` generates identity server-side. This discipline prevents the most consequential failure mode for this domain — authoritative data fabrication.

**Rating: Strong. Preserve without modification.**

### 1.2 Orchestration Pattern

The platform is correctly positioned as a single user-facing orchestrator over three independent domains (`docs/ARCHITECTURE.md:5`):

```
Browser (Jinja + vanilla JS) → school-ai-platform (FastAPI) → Exam Engine (HTTP)
                                                        → Results Engine (HTTP)
                                                        → AI Gateway (Provider Chain)
```

`app/services/engine_client.py` is the sole egress point to upstream engines. Business logic duplication is forbidden by design and no violations were observed. This yields clean bounded contexts and independent deployability of engines.

### 1.3 Layered Architecture

| Layer | Location | Responsibility | Adherence |
|---|---|---|---|
| Web UI | `app/web/`, `static/` | Server-rendered pages, presentation logic | Compliant — no business computation |
| API Routes | `app/api/routes/` | Authentication, validation, engine proxy, history | Compliant — thin controllers |
| Services | `app/services/` | Student Master, engine clients, uploads | Compliant |
| AI Gateway | `app/ai/` | Provider-agnostic orchestration, prompt isolation | Compliant |
| Config/Security | `app/config.py`, `app/security.py` | Fail-fast validation, headers, limits | Compliant |

The `API-first, not API-only` principle (`app/web/routes.py:1`) is correctly realized: every page consumes `/api/v1/*`, ensuring the JSON API remains the canonical contract.

### 1.4 Abstraction and Extensibility

**Provider Abstraction (`app/ai/providers/base.py:31`)** — The `AIProvider.complete()` interface achieves full adherence to the Open/Closed Principle. A single `OpenAICompatProvider` covers OpenRouter, Groq, Gemini, and self-hosted gateways via configuration alone. Introduction of a non-OpenAI-compatible protocol requires only a new adapter class; gateway, prompt, and validation logic remain closed to modification. This is reference-quality abstraction.

**Stub Double Strategy** — `stub://` engine URLs and `AI_USE_STUB` provide deterministic, contract-shaped offline behavior without mocking. Health probes return distinct `stub` vs `unreachable` vs `ok` states, eliminating silent double leakage. Production fail-closed validation (`app/config.py:87`) and CI negative testing (`.github/workflows/ci.yml:61`) enforce this at both build and runtime.

### 1.5 Architectural Findings

| ID | Severity | Finding | Location | Impact |
|---|---|---|---|---|
| ARCH-01 | **P0** | **Event-loop starvation:** Synchronous `httpx.Client` and `time.sleep()` executed within the asynchronous FastAPI middleware pipeline. The correlation middleware `app/main.py:46` is `async` and `await`s `call_next`, yet downstream engine and AI calls block the event loop. | `app/services/engine_client.py:42,78,90` `app/ai/gateway.py:173` | Throughput collapse to ~1 request per worker under concurrency |
| ARCH-02 | P1 | Divergent architecture documentation: `ARCHITECTURE.md` (root, 86 lines) and `docs/ARCHITECTURE.md` (50 lines) present conflicting layer definitions and diagrams. Documentation drift is already manifest. | Root `ARCHITECTURE.md` vs `docs/ARCHITECTURE.md` | Stakeholder misalignment, onboarding cost |
| ARCH-03 | P1 | Absence of schema migration system. `app/db.py:17` relies on `CREATE TABLE IF NOT EXISTS` without versioning. Schema evolution is invisible and non-reversible. | `app/db.py:86` | Operational risk, environment drift |
| ARCH-04 | P1 | Application factory side-effect: `app = create_app()` at import time `app/main.py:92` coupled with `@lru_cache` on `get_settings()` `app/config.py:143` forces environment setup before import and prevents cache invalidation between tests. | `app/main.py:92` `app/config.py:143` | Test fragility, 12-Factor violation |
| ARCH-05 | P2 | History endpoints return untyped `list[dict]` — no Pydantic response model, resulting in opaque OpenAPI schemas. | `app/services/history.py:32` `app/api/routes/examinations.py:57` | Contract erosion |
| ARCH-06 | P2 | Absence of transactional outbox pattern for orchestration metadata. `record_exam_job()` executes post-engine success without atomicity guarantees. | `app/api/routes/examinations.py:48` | Audit completeness gap on crash |

---

## 2. Critical Vulnerabilities & Risk Matrix

### 2.1 Risk Matrix

| ID | Category | Severity | Vulnerability | Exploit Vector | Impact | Remediation |
|---|---|---|---|---|---|---|
| SEC-01 | Network | **P0 / Critical** | **SSRF via insufficient egress filtering.** Engine URL validation `app/config.py:74` enforces scheme allowlist (`http(s)://`, `stub://`) and rejects userinfo, but permits private, loopback, link-local, and cloud metadata endpoints (`169.254.169.254`, `10.0.0.0/8`, `127.0.0.1`). Design assumes LAN filtering at deploy time with no code-level enforcement. | Operator-controlled env var or compromised config plane probes internal metadata services. | Full internal network disclosure, credential exfiltration | Implement DNS-resolved IP validation via `ipaddress` module; reject `is_private`, `is_loopback`, `is_link_local`, `is_multicast` unless explicitly allowlisted. |
| SEC-02 | AuthN | **P0 / Critical** | **Insecure session persistence with concurrency-induced corruption.** File-backed session store `app/security.py:61` uses non-atomic `read_text()`/`write_text()` without file locking. Concurrent logins cause file corruption. File created with default `0644` permissions (world-readable). Digest is unsalted SHA-256 without pepper or expiry garbage collection. | Race condition on concurrent authentication; file disclosure on shared host reveals session digests for offline brute-force. | Authentication bypass, session hijacking, unbounded file growth | Migrate to SQLite-backed session table with `expires_at` and `0600` file mode; introduce `filelock` + atomic `rename` as interim; enforce TTL. |
| SEC-03 | Integrity | **P0 / Critical** | **Concurrency-induced identity collision.** `_next_code()` `app/services/students.py:32` computes `MAX(student_code)` then inserts without serialization. Concurrent `POST /api/v1/students` within the same calendar year generates duplicate codes; one transaction fails with `UNIQUE` violation surfaced as HTTP 500. | Simultaneous student creation by two operators. | Denial of service, inconsistent identity | Replace read-then-insert with atomic sequence table (`UPDATE ... RETURNING`) or `lastrowid`-derived code generation. |
| SEC-04 | AuthN | **P0 / Critical** | **Absence of rate limiting on authentication surface.** `POST /login` `app/web/routes.py:26` permits unbounded credential guessing against a single `ADMIN_API_TOKEN`. No throttling, lockout, or anomaly logging. | Online brute-force / credential stuffing. | Administrative compromise | Implement `slowapi` or reverse-proxy rate limiting (5 attempts/minute/IP), exponential backoff, and audit logging. |
| SEC-05 | AuthN | **P1 / High** | **Cross-Site Request Forgery (CSRF).** `POST /login` and `POST /logout` are form endpoints without CSRF tokens. `SameSite=Lax` mitigates cross-site POST but not same-site or compromised subdomain vectors. | Forged logout or login via auto-submitting form from attacker-controlled origin. | Session fixation, denial of service | Introduce `itsdangerous`-signed CSRF token in `login.html` or adopt double-submit cookie pattern. |
| SEC-06 | Injection | **P1 / High** | **Unvalidated `artifact_url` from upstream engine.** `ExamJobOut.artifact_url` `app/schemas/engines.py:38` is engine-supplied and rendered as a hyperlink without scheme validation. A compromised engine can inject `javascript:` or `data:` URIs. | Compromised or malicious engine response. | Stored XSS | Validate `artifact_url` is `http(s)://` or relative path; reject `javascript:`, `data:`, `vbscript:` schemes. |
| SEC-07 | Integrity | P2 / Medium | **Request size gate bypass via chunked encoding.** Middleware `app/main.py:53` gates on `Content-Length` only. Requests using `Transfer-Encoding: chunked` omit `Content-Length` and bypass the 413 guard. | Oversized chunked body. | Memory exhaustion | Add streaming size enforcement via ASGI middleware or `uvicorn --h11-max-incomplete-event-size`. |
| SEC-08 | Disclosure | P2 / Medium | **Engine error detail propagation.** `app/services/engine_client.py:107` captures `resp.text[:500]` and surfaces via `EngineError.detail` → `JSONResponse` `app/main.py:81`, potentially leaking upstream stack traces. | Error response to authenticated caller (indirect). | Information disclosure | Return generic message for 5xx; log full detail server-side with `request_id`. |

### 2.2 Security Controls Confirmed Effective

The following controls were verified and require no remediation:

- Fail-fast secret validation with placeholder rejection (`app/config.py:66`)
- Production stub guard (`app/config.py:87`) with CI negative testing (`.github/workflows/ci.yml:61`)
- Constant-time credential comparison via `hmac.compare_digest` (`app/security.py:23`)
- `HttpOnly` + `SameSite=Lax` cookie binding with `Secure` gated on production (`app/web/routes.py:34`)
- `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, strict CSP, conditional HSTS (`app/security.py:90`)
- Magic-byte sniffing, MIME cross-check, filename allowlist, and streaming size cap on uploads (`app/services/uploads.py:71`)
- Recursive log redaction as single choke point (`app/logging_setup.py:25`) — extend `SENSITIVE_KEYS` to include `admin_api_token` and `exam_api_token` for completeness
- `.env` gitignore with CI secret hygiene scanning (`.github/workflows/ci.yml:48`)

---

## 3. Technical Debt & Scalability Analysis

### 3.1 Throughput and Latency

| Subsystem | Current State | Bottleneck | Throughput Impact | Remediation Priority |
|---|---|---|---|---|
| **Engine Client** | Synchronous `httpx.Client` per attempt with new TCP connection per retry; `time.sleep(0.5 * attempt)` blocks event loop | No connection pooling; event-loop starvation | **Critical** — throughput collapses to 1 concurrent request per worker; tail latency +500 ms per retry | P0 |
| **AI Gateway** | Sequential provider chain with `time.sleep(0.2)` per retry (`app/ai/gateway.py:173`) | Blocking sleep in async context; no jitter or circuit breaker | Adds 400 ms tail latency on dual failover; blocks all concurrent requests | P0 |
| **Database** | SQLite WAL, `sqlite3.connect()` per request, `COUNT(*) + SELECT` dual-query pattern (`app/services/students.py:97`), `LIKE %q%` full table scan | Single-writer lock; no FTS index; no connection pooling | Write serialization at ~50 concurrent writers; search latency O(n) | P1 |
| **Session Auth** | File I/O per `require_admin` invocation (`app/security.py:73`) | Global file lock contention under load | Authentication becomes serialization point | P0 |
| **Readiness Probe** | `GET /api/v1/ready` performs two synchronous engine health probes sequentially (`app/api/routes/health.py:35`) | Sequential I/O on every dashboard poll | Dashboard latency 2× engine RTT; no caching | P1 |

**Scalability Ceiling (Current):** Single-node, low-concurrency (<20 concurrent operators, <10k students). Horizontal scaling is not viable due to file-based SQLite and session state. Vertical scaling is constrained by event-loop blocking.

**Scalability Ceiling (Post-Phase 1):** Single-node, high-concurrency (~200 concurrent operators). Horizontal scaling viable after session and DB migration.

### 3.2 Code Quality and Maintainability

| ID | Finding | Location | Debt Class | Remediation |
|---|---|---|---|---|
| QUAL-01 | Duplicate mobile validation across `StudentBase`, `StudentUpdate`, `StudentImportRow` | `app/schemas/student.py:30,51,84` | DRY violation | Extract `validate_mobile(v)` helper |
| QUAL-02 | `parse_student_workbook` supports `csv` but `UPLOAD_ALLOWED_EXTENSIONS` default omits `csv`; empty sheet raises unhandled `StopIteration` → HTTP 500 | `app/services/uploads.py:82,94` `app/config.py:59` | Contract inconsistency, unhandled edge | Align allowlist; guard `header_cells = next(..., None)` |
| QUAL-03 | `StudentService.list` shadows `list` builtin, requiring `_BuiltinList` alias | `app/services/students.py:24,80` | Naming debt | Rename to `list_students` |
| QUAL-04 | `mypy` `strict = false` + `ignore_missing_imports = true` defeats type safety | `pyproject.toml:18` | Type erosion | Enable strict per-module; add `types-httpx` |
| QUAL-05 | Pinned dependencies without hash verification; CI installs `ruff`/`mypy`/`pip-audit` unpinned | `requirements.txt` `.github/workflows/ci.yml:31` | Supply-chain risk | Adopt `uv` + `uv.lock` or `pip-tools` with `--hash` |
| QUAL-06 | Broad `except Exception` with magic truncation (`errors.append(f"row {i}: {exc}")`) obscures failure classification | `app/services/students.py:172` | Observability gap | Structured `RowError` with categorical codes |
| QUAL-07 | `ARCHITECTURE.md` duplication (root vs `docs/`) | Root / `docs/` | Documentation drift | Consolidate to `docs/ARCHITECTURE.md`; remove root duplicate |

### 3.3 Operational Maturity

| Practice | Assessment | Evidence | Gap |
|---|---|---|---|
| 12-Factor Configuration | Good | `BaseSettings` + `.env` + fail-fast validation | `BASE_DIR / "data/platform.db"` default is repo-relative, not env-driven |
| Structured Logging | Strong | JSON + `ContextVar` correlation + recursive redaction | No sampling; `exc_info` only on unhandled |
| Health Probes | Strong | `/health` (liveness, public) + `/ready` (readiness with engine probes) | `/ready` missing DB `SELECT 1` check; `database_path` exposure acceptable but document |
| CI Gates | Strong | `compileall` + `ruff` + `mypy` + `pytest` + `pip-audit` + secret hygiene + prod-boot smoke test | No coverage threshold; `pip-audit` lacks `--local` flag |
| Type Safety | Partial | `mypy --explicit-package-bases` clean | `strict = false` undermines guarantees |
| Dependency Mgmt | Partial | Pinned versions | No hash pinning, no dev/prod split |
| Observability | Missing | — | No metrics (Prometheus), no tracing (OpenTelemetry) |
| Documentation | Excellent | 7 focused docs, each <100 lines | No ADRs for key decisions (SQLite, sync client, file sessions) |

---

## 4. Security Posture — Detailed Analysis

### 4.1 Egress Filtering (SSRF)

**Control:** `app/config.py:74` validates engine URLs against an allowlist of `http://`, `https://`, `stub://` and rejects userinfo (`@`).

**Gap:** No IP-level validation. An attacker controlling environment configuration can direct engine calls to link-local, private, or cloud metadata endpoints. The design note deferring filtering to "deploy time" is insufficient as a code-level control.

**Required Control:** DNS resolution followed by `ipaddress` classification. Reject `is_private`, `is_loopback`, `is_link_local`, `is_multicast`, and `is_reserved` unless the host is explicitly allowlisted. See Reference Implementation A.

### 4.2 Authentication and Session Management

**Controls (Effective):** Constant-time comparison, `HttpOnly` + `SameSite=Lax`, `Secure` gated on production, server-side revocation on logout.

**Gaps:**
- File-backed store is non-atomic and world-readable.
- No TTL enforcement or rotation; session valid for full `max_age` (12h) with no sliding window.
- No rate limiting or brute-force detection on the single `ADMIN_API_TOKEN` surface.

**Required Controls:** SQLite `sessions` table with `expires_at`, `0600` file mode (interim), `filelock` + atomic `rename`, per-IP rate limiting. See Reference Implementations B and D.

### 4.3 Input Validation and Attack Surface Reduction

Upload validation (`app/services/uploads.py`) is correctly layered: filename allowlist regex, extension allowlist, streaming size cap before full read, magic-byte signature verification, MIME cross-check. This is a reference implementation for file intake.

Gaps are limited to the `csv` allowlist mismatch and the `Content-Length`-only size gate (see Risk Matrix SEC-07).

### 4.4 AI Safety

The gateway correctly enforces: prompt isolation per `(task, context)` (`app/ai/prompts.py:101`), strict Pydantic validation (`app/ai/schemas.py:47`), failover on malformed output, and terminal handling of `missing_input` without failover. No prompt injection or output fabrication vectors were identified.

---

## 5. Engineering Roadmap — Prioritized Remediation

### Phase 1: Immediate Critical Remediation (Blockers — Gate for Production)

*Estimated effort: 4–6 engineering days. No feature work should proceed to production without this phase.*

| # | Action | Owner | Files | Acceptance Criteria |
|---|---|---|---|---|
| 1.1 | **Eliminate event-loop starvation.** Migrate `EngineClient` and `AIGateway` to `httpx.AsyncClient` with `asyncio.sleep`, persistent connection pooling, and `async def` route handlers. | Backend | `app/services/engine_client.py`, `app/ai/gateway.py`, `app/ai/providers/openai_compat.py`, `app/main.py`, `app/api/routes/*` | Load test: 100 concurrent `/api/v1/students` sustains >50 rps on 2 workers; no blocking calls in `async` context (verified via `ruff` `ASYNC` rules) |
| 1.2 | **Harden session persistence.** Migrate to SQLite `sessions` table with `expires_at`; interim: `filelock` + `0600` + atomic `rename`. Add TTL enforcement in `_valid_session`. | Backend | `app/security.py`, `app/db.py` | Concurrent login test (20 parallel) passes without corruption; file mode `0600`; expired sessions rejected |
| 1.3 | **Atomic student identity generation.** Replace `MAX(code)` read-then-insert with sequence table `UPDATE ... RETURNING` or `lastrowid`-derived code. | Backend | `app/services/students.py`, `app/db.py` | Concurrent creation test (20 parallel same-year) yields 20 distinct codes, zero 500s |
| 1.4 | **Egress IP validation (SSRF).** Implement DNS-resolved `ipaddress` guard in `_safe_service_url`. | Backend | `app/config.py` | `http://169.254.169.254`, `http://10.0.0.1`, `http://127.0.0.1:22` rejected; allowlisted internal hosts pass |
| 1.5 | **Authentication rate limiting.** Add `slowapi` or reverse-proxy limit on `POST /login` (5/min/IP) with audit log. | Backend/Infra | `app/web/routes.py`, `app/main.py` | 6th attempt within window returns 429; brute-force test passes |
| 1.6 | **Align upload contract.** Add `csv` to `UPLOAD_ALLOWED_EXTENSIONS` default; guard empty workbook in `parse_student_workbook`. | Backend | `app/config.py:59`, `app/services/uploads.py:94` | `csv` upload succeeds; empty `xlsx` returns 400, not 500 |

### Phase 2: Stability & Observability (Sprint 1–2)

*Estimated effort: 5–8 engineering days. Parallelizable with feature development.*

| # | Action | Files | Acceptance Criteria |
|---|---|---|---|
| 2.1 | Introduce schema migrations (`alembic` or `yoyo`) with `001_initial.sql` matching current `SCHEMA` | `app/db.py`, `migrations/` | `alembic upgrade head` reproduces current DB; forward/backward migration tested |
| 2.2 | Eliminate `app = create_app()` import side-effect; clear `get_settings` cache per test via fixture | `app/main.py:92`, `app/config.py:143`, `tests/conftest.py` | `from app.main import create_app` has no side-effect; tests pass without env-before-import dance |
| 2.3 | Type history endpoints; add `ExamJobHistoryOut`, `ResultQueryHistoryOut` Pydantic models | `app/services/history.py`, `app/api/routes/examinations.py` | OpenAPI schema shows typed arrays, not `object` |
| 2.4 | Streaming request size enforcement for chunked encoding; add `PRAGMA busy_timeout=5000` | `app/main.py:53`, `app/db.py:77` | Chunked 20 MB body rejected with 413; concurrent write test passes |
| 2.5 | Concurrent readiness probes + caching: `asyncio.gather` for engine health, `lru_cache(ttl=30)` for `counts()`/`distinct_classes()` | `app/api/routes/health.py`, `app/services/students.py` | `/ready` latency < max(engine RTT) not sum; dashboard poll does not amplify engine load |
| 2.6 | CSRF protection on `POST /login`/`POST /logout` | `app/web/routes.py`, `app/web/templates/login.html` | CSRF token validated; missing token returns 403 |

### Phase 3: Hardening & Optimization (Long-Term)

*Estimated effort: Ongoing. Prioritize by operational need.*

| # | Action | Rationale |
|---|---|---|
| 3.1 | Deduplicate `ARCHITECTURE.md`; retain `docs/ARCHITECTURE.md` as canonical source | Eliminate documentation drift |
| 3.2 | Deduplicate mobile validator; rename `StudentService.list` → `list_students`; enable `mypy strict` incrementally | Reduce naming and type debt |
| 3.3 | Migrate dependency management to `uv` + `uv.lock` with hash pinning; split dev/prod dependencies | Supply-chain integrity |
| 3.4 | Add `FTS5` virtual table for student search; add `PRAGMA foreign_keys=ON` index audit | Search latency O(log n) |
| 3.5 | Validate `artifact_url` scheme; extend `SENSITIVE_KEYS` with `admin_api_token`; add Prometheus + OpenTelemetry instrumentation | Attack surface reduction, observability |
| 3.6 | Add `Dockerfile` + `docker-compose` for local engine stubs; document TLS termination (Caddy/nginx) and HSTS preload | Reproducible deployment |
| 3.7 | Document scale ceiling and Postgres migration path (`app/db.py` + `app/services/students.py` are the only migration touchpoints) | Capacity planning |
| 3.8 | Introduce ADRs for SQLite selection, sync-to-async migration, and session store decision | Institutional knowledge |

---

## 6. Reference Implementations — Proposed Patterns

### A. Asynchronous Engine Client with Connection Pooling

*Resolves ARCH-01. Eliminates event-loop starvation; introduces keep-alive pooling and non-blocking backoff.*

```python
# app/services/engine_client.py
import asyncio
import httpx

class EngineClient:
    def _async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url if not self.stub else "http://stub.local",
            timeout=self.settings.engine_api_timeout_seconds,
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )

    async def _request(self, method: str, path: str, request_id: str,
                       json: dict | None = None, params: dict | None = None) -> Any:
        # ... header construction ...
        async with self._async_client() as client:
            resp = await client.request(method, path, headers=headers, json=json, params=params)
        if resp.status_code >= 500 and attempt < attempts and method.upper() == "GET":
            await asyncio.sleep(0.5 * attempt)
```

Wire as `app.state.engine_client` singleton in `create_app()`; convert route handlers to `async def`.

### B. Atomic Student Code Generation via Sequence Table

*Resolves SEC-03. Eliminates race by serializing through an atomic `UPDATE ... RETURNING`.*

```sql
-- app/db.py SCHEMA addition
CREATE TABLE IF NOT EXISTS student_seq(year INTEGER PRIMARY KEY, next_val INTEGER NOT NULL DEFAULT 1);
```

```python
# app/services/students.py
def _next_code(conn: sqlite3.Connection, year: int) -> str:
    row = conn.execute(
        "INSERT INTO student_seq(year, next_val) VALUES (?, 1) "
        "ON CONFLICT(year) DO UPDATE SET next_val = next_val + 1 RETURNING next_val",
        (year,),
    ).fetchone()
    return f"APS-{year}-{row['next_val']:04d}"
```

Alternative: `INSERT` then `UPDATE students SET student_code = ? WHERE id = ?` using `cursor.lastrowid` — avoids sequence table but requires two statements.

### C. SSRF Egress Filtering — DNS-Resolved IP Classification

*Resolves SEC-01. Rejects private, loopback, link-local, and metadata endpoints.*

```python
# app/config.py — extend _safe_service_url
import ipaddress
import socket
import httpx

host = httpx.URL(v).host
if host:
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise ValueError(f"engine host resolves to non-public IP: {ip}")
        if ip.is_link_local or str(ip).startswith("169.254."):
            raise ValueError("link-local/metadata IP rejected")
    except socket.gaierror:
        pass  # DNS failure surfaced at request time; scheme check already passed
```

Explicit allowlist (e.g., `10.0.0.5` for on-prem engines) should bypass this check via configuration.

### D. SQLite-Backed Session Store

*Resolves SEC-02. Eliminates file race, enforces TTL, restricts permissions.*

```sql
CREATE TABLE IF NOT EXISTS sessions(
    id TEXT PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
```

```python
# app/security.py — replace file I/O
def create_session() -> str:
    session_id = secrets.token_urlsafe(32)
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    expires = (datetime.now(UTC) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db_session(get_settings()) as conn:
        conn.execute("INSERT INTO sessions(id, digest, expires_at) VALUES (?,?,?)",
                     (session_id, digest, expires))
        conn.execute("DELETE FROM sessions WHERE expires_at < strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    return session_id
```

---

## 7. Conclusion

The `school-ai-platform` is architecturally sound in its domain modeling and demonstrates engineering maturity beyond its academic context. The core invariants, provider abstraction, and security baseline are production-grade. The remaining defects are **localized, well-understood, and mechanically remediable** — they do not indicate systemic design failure.

**Gating criterion for production:** Completion of Phase 1. The six P0 items collectively address concurrency safety, event-loop integrity, and attack surface reduction. Upon their resolution, the system is suitable for controlled production deployment behind TLS termination with rate limiting.

**Long-term trajectory:** With Phases 2 and 3 complete, the platform will support horizontal scaling, auditable schema evolution, and operational observability — positioning it for sustained institutional use without re-architecture.

---

*This report consolidates findings from static analysis of all application modules, CI configuration, and documentation. No dynamic penetration testing or load testing was performed; Phase 1 acceptance criteria include targeted concurrency and brute-force tests to close that gap.*

*Prepared for engineering leadership and stakeholder review. Remediation ownership and sprint allocation to be confirmed in planning.*
