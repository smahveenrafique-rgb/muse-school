# Security

## Threat model (what we protect against)

- Secret leakage (provider keys, admin token) to browser, logs, or git.
- Student PII exposure to unauthenticated users.
- Malicious file uploads (wrong type, oversized, path traversal).
- AI output treated as truth (hallucinated marks, invented students).
- Upstream engine compromise reflected blindly into official records.

## Controls

| Risk | Control |
|---|---|
| Secrets in frontend | Keys live only in server env; `/settings` renders a redacted view; CSP blocks exfiltration vectors |
| Secrets in git | `.env` gitignored; `.env.example` has placeholders; startup validator rejects placeholder secrets |
| Unauthenticated access | Every API route and page (except `/login`, `/health`) requires admin auth: `Bearer` token or HttpOnly session cookie |
| Session theft | Random 256-bit session ids, sha256-hashed at rest, HttpOnly + SameSite=Lax cookies, server-side revocation on logout |
| Upload abuse | Filename allowlist regex, extension allowlist, magic-byte sniffing, MIME cross-check, size cap enforced during streaming read |
| Oversized requests | `MAX_REQUEST_BYTES` (5 MiB default): `Content-Length` checked by middleware → HTTP 413 before the body is read |
| Stub in production | Startup guard refuses `stub://` engines and `AI_USE_STUB=true` when `APP_ENV=production`; CI tests both directions |
| SSRF via engine URLs | Only `http://`, `https://`, `stub://` schemes accepted; userinfo (`user@host`) rejected |
| AI hallucination | Strict JSON schema validation; figures may only repeat engine-supplied numbers; `missing_input` surfaces instead of guessing |
| Log leakage | `redact()` masks `api_key/token/secret/password/…` recursively on every structured log field |
| Clickjacking / MIME sniffing | `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, tight CSP; HSTS in production |
| Dependency risk | Pinned `requirements.txt`; CI runs `pip-audit` |

## Authentication

Two equivalent paths, one verifier (`require_admin`):

1. **API clients**: `Authorization: Bearer <ADMIN_API_TOKEN>`.
2. **Browser**: POST `/login` with the token → HttpOnly session cookie.

Tokens are compared with `hmac.compare_digest` (constant time).

## What is intentionally NOT here

- No per-user accounts or roles yet — single admin token. Add RBAC as a
  designed workstream if multi-operator access is needed.
- No rate limiting — put the app behind a reverse proxy (Caddy/nginx)
  with rate limits for internet exposure.
