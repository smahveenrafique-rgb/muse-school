# Production gate

Rules that keep test doubles and secrets out of production.
Enforced in code (`app/config.py`), in CI (`.github/workflows/ci.yml`),
and at startup — not by convention.

## The guard

`APP_ENV=production` triggers `Settings._production_stub_guard()`:

- refuses `EXAM_API_BASE_URL` / `RESULTS_API_BASE_URL` starting with
  `stub://`
- refuses `AI_USE_STUB=true`

Violation → `pydantic.ValidationError` at startup → the process does
not boot. There is no flag to bypass it; the only way to run production
is to configure real engines (or deliberately unreachable real URLs,
which boot with `stub_mode: false` and `status: "unreachable"`).

## Secrets

- Required in production: `APP_SECRET_KEY`, `ADMIN_API_TOKEN`
  (minimum lengths enforced), engine tokens, AI provider keys.
- Provider keys never reach the browser: the AI gateway calls providers
  server-side; the Settings page shows connectivity status, never values.
- `.env` is git-ignored and excluded from release zips. CI fails if a
  `.env` file is present or a secret-looking pattern appears in tracked
  source.

## Request limits

- `MAX_REQUEST_BYTES` (default 5 MiB): oversized `Content-Length` is
  rejected with HTTP 413 before the body is read.

## Pre-deploy checklist

1. `APP_ENV=production`, real secrets set, no `stub://` URLs,
   `AI_USE_STUB=false`.
2. `pip install -r requirements.txt` on Python 3.12.
3. `ruff check app tests` — clean.
4. `mypy --explicit-package-bases app` — clean.
5. `python -m pytest tests/ -q` — green.
6. `pip-audit -r requirements.txt` — no known vulnerabilities.
7. Boot once and check `/api/v1/ready`: `environment: production`,
   `stub_mode: false`, engines `ok`/`unreachable` (never `stub`).

CI runs all of the above, including the negative test (production +
stubs must fail to boot) and a live production boot smoke test.
