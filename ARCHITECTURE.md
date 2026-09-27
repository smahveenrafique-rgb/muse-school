# Architecture

## Topology

```
Browser (Jinja pages + vanilla JS)
    │  HTTPS, /api/v1/* JSON only
    ▼
School AI Platform (FastAPI, this repo)
    ├── Student Master ──► SQLite (students, audit_log)
    ├── API layer ──► Exam Engine API        (papers, jobs)
    │              └─► Results Engine API    (marks, grades, results)
    └── AI Gateway ──► Provider 1 (primary)
                    ──► Provider 2 (secondary, on failover)
                    ──► Provider 3 (tertiary, on failover)
```

## Module boundaries

| Module | Owns | Must never |
|---|---|---|
| `app/web` | HTML pages, navigation | Business logic, secrets |
| `app/api/routes` | Request validation, auth, error mapping | Engine/AI internals |
| `app/services/students.py` | Student identity (`APS-YYYY-NNNN`) | Marks, results |
| `app/services/engine_client.py` | Typed HTTP calls, retries, stub doubles | Business rules |
| `app/ai/gateway.py` | Task routing, failover, validation | Inventing data |
| `app/ai/prompts.py` | Server-side system prompts per (task, exam type) | Client-supplied prompts |
| `app/ai/providers/*` | One adapter per provider protocol | Keys in logs/responses |

## Key decisions

1. **Single deployable.** FastAPI serves both the JSON API and the HTML
   frontend. No Node build step, no separate frontend deploy — the whole
   platform runs as one process (`uvicorn app.main:app`).

2. **API-first, not API-only.** Every page fetches `/api/v1/*`. The HTML is
   replaceable; the API is the product.

3. **SQLite for Student Master.** The student registry is small, single-writer,
   and needs zero operations. WAL mode is enabled. If it ever outgrows
   SQLite, only `app/db.py` and `app/services/students.py` change.

4. **One provider protocol.** All major LLM providers expose (or can be
   reached through) an OpenAI-compatible `/chat/completions` endpoint, so a
   single adapter covers OpenRouter, Groq, Gemini, and self-hosted gateways.
   A new *protocol* means a new adapter class, not gateway changes.

5. **Stub doubles, not mocks, at the boundary.** `stub://` engine URLs and
   `AI_USE_STUB=true` give deterministic offline behaviour with payloads
   shaped exactly like the real contracts. Tests and local dev never need
   the real engines.

6. **Fail-closed validation.** Unknown, malformed, or schema-invalid AI
   output is rejected and failed over — never repaired, never silently
   accepted. A `missing_input` refusal surfaces immediately as a controlled
   error instead of failing over to a provider that might invent data.

7. **No hard deletes.** Students are deactivated (`status`), never deleted —
   examination and result history must keep resolving their codes.

## Request lifecycle (AI task)

```
POST /api/v1/ai/run {task, exam_type, input}
  → auth (admin)
  → router validates (task, exam_type)
  → build_system_prompt(task, exam_type)      # server-side, isolated per exam type
  → for each provider in chain:
  │     complete() → extract JSON → validate against task schema
  │     on timeout/quota/rate-limit/transport/malformed → next provider
  → all failed → 502 with controlled error (no invented data)
```

## Correlation

Every inbound request gets an `X-Request-ID` (or reuses the caller's). It
propagates to engine calls (`X-Request-ID`), AI provider calls, and all log
lines, so one ID traces a user action end to end.

## What this repo is NOT

- Not the Exam Engine (paper generation, marks schemes).
- Not the Results Engine (totals, percentages, grades, pass/fail).
- Not an ERP (no fees, attendance, payroll, teacher management).
- Not a place for business-rule invention — the AI drafts and summarises;
  engines decide.
