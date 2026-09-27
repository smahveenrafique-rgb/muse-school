# Deployment

## Requirements

- Python 3.12+
- The two engine base URLs (real `http(s)://` URLs in production —
  `stub://` doubles are development-only and **refused** in production)
- LLM provider keys (or `AI_USE_STUB=true` in development only)

## Production run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in REAL secrets; never commit .env
APP_ENV=production uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
```

In production:
- API docs are disabled (`/api/docs` → 404).
- `Strict-Transport-Security` is emitted (terminate TLS at the reverse proxy).
- Serve behind Caddy/nginx for TLS, rate limiting, and static caching.

## Data

- Student Master lives in `DATABASE_PATH` (default `data/platform.db`, WAL mode).
- Back up the SQLite file on your existing backup schedule; the audit log
  table records every student mutation.
- Uploads are validated in memory and never persisted to disk.

## Health

- `GET /api/v1/health` — liveness (public).
- `GET /api/v1/ready` — readiness: database path, engine URLs, AI provider
  count, stub mode (public; contains no secrets).

## Deployment

```bash
APP_ENV=production APP_SECRET_KEY=<random> ADMIN_API_TOKEN=<random> \
EXAM_API_BASE_URL=https://exam.internal \
RESULTS_API_BASE_URL=https://results.internal \
AI_PRIMARY_PROVIDER=openrouter AI_PRIMARY_MODEL=… \
AI_PRIMARY_BASE_URL=https://openrouter.ai/api/v1 AI_PRIMARY_API_KEY=… \
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
```
