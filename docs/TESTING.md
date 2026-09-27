# Testing

```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

## Suite map (81 tests)

| File | Covers |
|---|---|
| `test_students.py` | CRUD, server-generated codes, mobile validation, search/filter, deactivation, import, DELETE→405, retry dedupe (no dupes), real dashboard counts |
| `test_engines.py` | Exam job submit/poll, results passthrough (figures untouched), 422 on bad enums, 401, unreachable engine → 502 |
| `test_ai_gateway.py` | Failover matrix (timeout→secondary, 2 failures→tertiary, all-fail→controlled error), malformed→failover, schema violation→failover, missing-input→immediate controlled error, prompt isolation per context, API surface |
| `test_uploads.py` | Extension, magic bytes, MIME, size cap, filename safety, xlsx parsing, endpoint round-trip (anyio/asyncio) |
| `test_security.py` | Auth gates, login/logout session flow, secure headers, no secret leakage in `/settings`, request-ID correlation, oversized body → 413 |
| `test_logging.py` | Recursive redaction of sensitive keys |
| `test_integration_hardening.py` | Production stub refusal, URL scheme/userinfo validation, explicit stub state, engine health probes, exam/result history, safe activity feed, AI RESULTS/GENERAL contexts, forbidden task/context pairs |

## Quality gates (all run in CI)

```bash
python -m compileall -q app tests
ruff check app tests          # clean — no warnings
mypy --explicit-package-bases app   # clean
python -m pytest tests/ -q    # 81 passed
pip-audit -r requirements.txt # no known vulnerabilities
```

Plus secret hygiene (no `.env`, no secret patterns in tracked files)
and production startup validation: production + stubs must **fail** to
boot; production + real URLs must boot with `stub_mode: false`.
See `docs/PRODUCTION_GATE.md`.

## Conventions

- `tests/conftest.py` builds an isolated app per test: temp SQLite file,
  `stub://` engines, `AI_USE_STUB=true`. No network, no real keys.
- Engine client tests use the stub doubles — they assert **platform**
  behaviour at the boundary, not engine logic.
- AI failover tests inject scripted `StubProvider`s (timeout, 429,
  malformed) — the full matrix runs offline.
- When adding a feature: add the test first, then the code, then run the
  whole suite. CI runs the same command.
