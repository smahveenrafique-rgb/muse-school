# Configuration

All settings come from the environment (or `.env` in development — see
`.env.example`). The app **fails fast at startup** if secrets are missing
or still placeholders.

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `development`/`staging`/`production` |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | bind address |
| `APP_SECRET_KEY` | *(required)* | ≥16 chars, never the placeholder |
| `ADMIN_API_TOKEN` | *(required)* | ≥16 chars; used for login + `Bearer` auth |
| `DATABASE_PATH` | `data/platform.db` | SQLite file for Student Master |
| `EXAM_API_BASE_URL` | `http://127.0.0.1:8100` | `stub://exam-engine` for offline dev |
| `RESULTS_API_BASE_URL` | `http://127.0.0.1:8200` | `stub://results-engine` for offline dev |
| `ENGINE_API_TIMEOUT_SECONDS` | `30` | per-request timeout to engines |
| `ENGINE_API_MAX_RETRIES` | `2` | retries for safe (GET) calls |
| `AI_PRIMARY_PROVIDER` / `_MODEL` / `_BASE_URL` / `_API_KEY` | empty | e.g. `openrouter` |
| `AI_SECONDARY_*` / `AI_TERTIARY_*` | empty | failover tiers; empty = skipped |
| `AI_TIMEOUT_SECONDS` | `45` | per-provider timeout |
| `AI_MAX_RETRIES` | `1` | retries per tier before failover |
| `AI_USE_STUB` | `false` | `true` = deterministic offline stub |
| `UPLOAD_MAX_BYTES` | `10485760` | 10 MiB |
| `MAX_REQUEST_BYTES` | `5242880` | 5 MiB — oversized bodies → HTTP 413 before reading |
| `UPLOAD_ALLOWED_EXTENSIONS` | `xlsx,xls,pdf,png,jpg,jpeg` | csv can be added here |
| `LOG_LEVEL` | `INFO` | |

Engine URLs accept only `http://`, `https://`, or the explicit
`stub://` scheme; exotic schemes and userinfo (`user@host`) are
rejected. **Production refuses stubs**: with `APP_ENV=production`,
`stub://` engine URLs or `AI_USE_STUB=true` abort startup —
see `docs/PRODUCTION_GATE.md`.

## Provider examples

**OpenRouter** (primary):
```bash
AI_PRIMARY_PROVIDER=openrouter
AI_PRIMARY_MODEL=openai/gpt-4o-mini
AI_PRIMARY_BASE_URL=https://openrouter.ai/api/v1
AI_PRIMARY_API_KEY=sk-or-v1-…
```

**Groq** (secondary):
```bash
AI_SECONDARY_PROVIDER=groq
AI_SECONDARY_MODEL=llama-3.3-70b-versatile
AI_SECONDARY_BASE_URL=https://api.groq.com/openai/v1
AI_SECONDARY_API_KEY=gsk_…
```

**Gemini** via its OpenAI-compatible endpoint (tertiary):
```bash
AI_TERTIARY_PROVIDER=gemini
AI_TERTIARY_MODEL=gemini-2.0-flash
AI_TERTIARY_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
AI_TERTIARY_API_KEY=AIza…
```
