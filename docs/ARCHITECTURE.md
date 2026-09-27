# Architecture

## Position

`school-ai-platform` is the single user-facing application over three
independent domains. It **orchestrates**; it does not own academic logic.

```
                    ┌─────────────────────┐
                    │  school-ai-platform │
                    │  (this repo)        │
                    └────────┬────────────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     Student Master   Exam Engine    Results Engine
     (owned here)     (independent)  (independent)
     SQLite           HTTP API       HTTP API
```

The Exam Engine and Results Engine are separate repositories and
deployments. This platform talks to them only through
`app/services/engine_client.py`. Copying their business logic here is
forbidden by design — the platform renders engine figures verbatim.

## Layers

| Layer | Location | Role |
|---|---|---|
| Web UI | `app/web/templates/`, `static/` | Server-rendered pages + presentation JS. Never computes academic data. |
| API routes | `app/api/routes/` | Thin: auth, validation, proxy to engines, history recording. |
| Services | `app/services/` | Student Master, engine clients, uploads, history. |
| AI gateway | `app/ai/` | Provider-agnostic drafts/summaries with prompt isolation. Never authoritative. |
| Config/security | `app/config.py`, `app/security.py` | Fail-fast settings, production stub guard, headers, request limits. |

## Data

- `data/platform.db` (SQLite, WAL): students, audit log, exam job
  submission metadata, result query metadata.
- Authoritative marks/results live in the Results Engine, not here.
  The platform's history tables store **metadata only**.
- Uploads are validated in memory and never persisted.

## Key invariants

1. Frontend and AI never calculate marks, totals, grades, or pass/fail.
2. Stubs are explicit (`stub://`, `AI_USE_STUB`) and refused in production.
3. Every AI output is schema-validated; `missing_input` never fails over.
4. Secrets never reach the browser; Settings shows status, not values.
5. Student IDs are server-generated and immutable; deletion is
   deactivation-only.
