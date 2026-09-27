# School AI Platform

The single user-facing application for **Student Master**, the **Examination
System**, and the **Results System** — plus a provider-agnostic **AI Gateway**.

The Examination Engine and Results Engine are independent backend systems.
This platform **never** reimplements their logic: it talks to them only
through stable HTTP APIs (see `docs/API.md`).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in real secrets
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 — sign in with the `ADMIN_API_TOKEN` from `.env`.

For offline development (no engines, no AI keys):

```bash
EXAM_API_BASE_URL=stub://exam-engine \
RESULTS_API_BASE_URL=stub://results-engine \
AI_USE_STUB=true uvicorn app.main:app
```

## Layout

| Path | Responsibility |
|---|---|
| `app/main.py` | App factory, middleware, router wiring |
| `app/config.py` | Validated environment configuration (fails fast) |
| `app/api/routes/` | JSON API: students, examinations, results, ai, uploads, health |
| `app/web/` | Thin server-rendered pages (Dashboard, Students, Examinations, Results, Settings) |
| `app/services/students.py` | Student Master (SQLite, server-generated IDs) |
| `app/services/engine_client.py` | Typed Exam/Results engine clients + stub doubles |
| `app/ai/` | AI Gateway: router, prompts, provider adapters, failover, validation |
| `static/` | Design system CSS + frontend JS (no build step) |
| `tests/` | 81 tests: unit, API, failover matrix, security, uploads, integration hardening |

## Documentation

- `docs/ARCHITECTURE.md` — system design and boundaries
- `docs/API.md` — engine contracts + platform API surface
- `docs/AI_GATEWAY.md` — provider model, failover, prompt contexts
- `docs/CONFIGURATION.md` — every environment variable
- `docs/DEPLOYMENT.md` — production deployment
- `docs/SECURITY.md` — threat model and controls
- `docs/TESTING.md` — how to run and extend tests
- `docs/INTEGRATION.md` — Exam/Results engine integration guide
- `docs/PRODUCTION_GATE.md` — stub/secret guards and pre-deploy checklist

## Design principles

- **API-first**: the browser only talks to `/api/v1/*`; pages are thin views.
- **Small and modular**: no ERP creep. Student Master holds only what
  examinations and results need.
- **Deterministic core**: marks, totals, grades, pass/fail are computed
  exclusively by the engines. The AI assists; it never decides.
- **Secrets stay server-side**: provider keys never reach the browser.
