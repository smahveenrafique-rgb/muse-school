# Integration guide — Exam Engine & Results Engine

The platform integrates with two **independent** systems over HTTP.
It never duplicates their business logic and never computes
marks, totals, percentages, grades, or pass/fail itself.

## Architecture position

```
browser ──► school-ai-platform ──► Exam Engine      (paper generation)
                              ──► Results Engine   (marks / report data)
                              ──► AI providers     (drafts & summaries only)
```

- `app/services/engine_client.py` is the only module that speaks to the
  engines: `ExamEngineClient`, `ResultsEngineClient`.
- Routes in `app/api/routes/examinations.py` / `results.py` are thin
  proxies plus **metadata history** (see below).
- The frontend (`static/js/`) renders engine responses verbatim.

## Configuration

| Variable | Meaning |
|---|---|
| `EXAM_API_BASE_URL` | Exam Engine base URL, or `stub://…` for offline dev |
| `RESULTS_API_BASE_URL` | Results Engine base URL, or `stub://…` for offline dev |
| `EXAM_API_TOKEN` / `RESULTS_API_TOKEN` | Bearer tokens, sent server-side only |
| `ENGINE_TIMEOUT_SECONDS` | Per-request timeout |
| `AI_USE_STUB` | `true` forces the deterministic AI stub |

URL validation (`app/config.py`): only `http://`, `https://`, and the
explicit `stub://` scheme are accepted; exotic schemes and URLs with
`userinfo` (`https://user@host`) are rejected — SSRF hygiene.

## Stub mode (explicit, never silent)

`settings.stub_mode` is `True` when any engine URL is `stub://…` or
`AI_USE_STUB=true`. It is surfaced in:

- `GET /api/v1/ready` → `stub_mode`
- the dashboard warning banner
- `Settings → Connectivity`

Stub engines return `status: "stub"` from `health()` probes.
Unreachable real engines return `status: "unreachable"` — a controlled
state, not a crash. **Production refuses stubs entirely**
(see `PRODUCTION_GATE.md`).

## Platform-side history (metadata only)

The platform keeps orchestration metadata so operators can audit what
was asked, without ever storing authoritative academic data:

- `exam_job_submissions` — job id, exam type, class, subject, timestamps
- `result_query_log` — scope, class, subject, timestamp

`GET /api/v1/examinations/history` and `GET /api/v1/results/history`
serve these logs. The recent-activity feed (`app/services/history.py`)
excludes full student records and phone numbers.

## Contract verification without live engines

Real Exam/Results APIs were not available during this build. Offline
stubs in `engine_client.py` verify the contract shape and the full
request/response workflow; the test-suite (`tests/test_examinations.py`,
`tests/test_results.py`, `tests/test_integration_hardening.py`) pins the
proxy behaviour, history recording, and unreachable-engine handling.

What this does **not** claim: real external integration was verified.
Point the URLs at the real engines and watch `/api/v1/ready` —
`stub_mode: false` and engine `status` fields tell the truth.
