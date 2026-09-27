# AI Gateway

Provider-agnostic orchestration for AI-assisted workflows. The gateway
**assists** — drafting outlines, summarising engine data, drafting parent
messages. It never decides marks, identity, totals, grades, or pass/fail.

## Tasks

| Task | Purpose | Output schema |
|---|---|---|
| `draft_exam_outline` | Topic/question outline for teacher review | `ExamOutline` |
| `summarize_results` | Plain-language summary of engine result rows | `ResultsSummary` |
| `parent_message` | Short respectful parent message from engine figures | `ParentMessage` |

New tasks: add a prompt to `app/ai/prompts.py`, a schema to
`app/ai/schemas.py`, and tests. Never add tasks that compute outcomes.

## Provider model

```
AIGateway
 └── AIProvider (interface: app/ai/providers/base.py)
       ├── OpenAICompatProvider   # OpenRouter, Groq, Gemini, self-hosted
       └── StubProvider           # deterministic, for tests/offline dev
```

All supported providers speak OpenAI-compatible `/chat/completions`, so one
adapter covers them — providers differ only by configuration:

```bash
AI_PRIMARY_PROVIDER=openrouter
AI_PRIMARY_MODEL=openai/gpt-4o-mini
AI_PRIMARY_BASE_URL=https://openrouter.ai/api/v1
AI_PRIMARY_API_KEY=sk-or-…
```

Leave a tier empty to shorten the chain. Keys stay server-side and are
never sent to the browser.

## Failover

```
primary ──(timeout | quota | rate-limit | transport | malformed)──► secondary
        ──(same)──► tertiary ──(same)──► controlled 502, no invented data
```

- Each tier is retried `AI_MAX_RETRIES` times for transient errors.
- Malformed JSON / schema violations fail over — never silently accepted.
- A `missing_input` refusal surfaces **immediately** as a controlled error;
  failing over would risk another provider inventing the missing data.

## Prompt routing

`build_system_prompt(task, context)` selects exactly one server-side
prompt. Contexts: `CAT-1`, `CAT-2`, `FINAL`, `RESULTS`, `GENERAL`.
Context is isolated: the CAT-1 prompt never mentions CAT-2/FINAL and
vice versa. Allowed task/context combinations are enforced in
`app/ai/prompts.py` before any provider is contacted:

- `draft_exam_outline`: CAT-1, CAT-2, FINAL only
- `summarize_results`: CAT-1, CAT-2, FINAL, RESULTS
- `parent_message`: all five contexts

`GET /api/v1/ai/contexts` lists the allowed contexts per task.
Every prompt enforces the hard rules:

1. Single valid JSON object, no outside prose.
2. Never invent marks, totals, grades, pass/fail, students, or records.
3. Never invent examination business rules.
4. No code modification.
5. Missing input → `{"error": "missing_input", "needed": [...]}`.

## Validation

`_extract_json` tolerates markdown code fences (courtesy, not leniency);
the extracted object is then strictly validated with pydantic. Anything
else → reject → retry/failover.

## Logging (per attempt)

`provider, model, request_id, task, success/failure, latency_ms,
failover event, validation result, output_chars`.

Never logged: API keys, credentials, full prompts, full AI payloads,
unnecessary student PII. `app/logging_setup.py::redact` is the choke point.

## Offline mode

`AI_USE_STUB=true` forces the deterministic stub for every tier — no
network, no keys. Used by tests and local development.
