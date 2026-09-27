# Engine API contracts

This is the **stable boundary** between the platform and the two engines.
The platform implements the client side (see
`app/services/engine_client.py`); the engines implement the server side.
Either side may evolve internals freely as long as these contracts hold.

> Status: the real engine HTTP APIs are not yet deployed. Until they are,
> point the platform at the stub doubles (`stub://exam-engine`,
> `stub://results-engine`), which return canned payloads in exactly these
> shapes.

## Common rules

- Base URLs from environment: `EXAM_API_BASE_URL`, `RESULTS_API_BASE_URL`.
- JSON everywhere; UTF-8.
- Every request carries `X-Request-ID` (correlation).
- `POST` requests carry `Idempotency-Key` (safe retry).
- `GET`s are retried on 5xx/timeout (`ENGINE_API_MAX_RETRIES`).

## Exam Engine

### `POST /api/v1/exam-jobs` — submit a generation job

Request:

```json
{
  "exam_type": "CAT-1 | CAT-2 | FINAL",
  "class_name": "5",
  "subject": "Mathematics",
  "options": {},
  "student_codes": []
}
```

- `options`: opaque engine-defined knobs (template id, etc.). The platform
  passes them through without interpretation.
- `student_codes`: roster subset; empty means the whole class.

Response `200`:

```json
{
  "job_id": "job-9f3a…",
  "status": "queued | running | succeeded | failed",
  "exam_type": "CAT-1",
  "detail": "human-readable note",
  "artifact_url": "https://engine/…/paper.pdf"
}
```

### `GET /api/v1/exam-jobs/{job_id}` — poll job status

Response: same shape as above. `artifact_url` is populated on `succeeded`.

## Results Engine

### `GET /api/v1/results` — query results

Query params: `scope=CAT-1|CAT-2|FINAL|CONSOLIDATED`, `class` (required),
`subject` (optional), `student_code` (optional).

Response `200`:

```json
{
  "items": [
    {
      "student_code": "APS-2026-0042",
      "student_name": "Ali Raza",
      "class_name": "5",
      "subject": "Mathematics",
      "exam_type": "CAT-1",
      "marks_obtained": 20.0,
      "total_marks": 25.0,
      "percentage": 80.0,
      "grade": "A",
      "result": "PASS"
    }
  ],
  "generated_at": "2026-09-26T10:00:00Z",
  "source": "results-engine"
}
```

**The platform treats every numeric/string outcome as opaque.** It renders
`marks_obtained`, `total_marks`, `percentage`, `grade`, and `result`
exactly as received and never recomputes them.

## Versioning

Contracts are versioned by URL prefix (`/api/v1/`). Breaking changes
require a new prefix; the platform pins the version it speaks.

---

# Platform API surface

The platform's own routes (all under `/api/v1/`, admin-token gated
unless noted). None of these compute marks, grades, or pass/fail.

## Students

- `POST /api/v1/students` — create (stable generated `student_code`)
- `GET /api/v1/students` — list with filters; `counts` endpoint returns
  real total/active/inactive aggregates
- `PATCH /api/v1/students/{code}` — update; deactivation only, no delete
- `POST /api/v1/students/import` — xlsx/csv import; retries dedupe on the
  case-insensitive natural key (name + father/guardian + class + section)
  and report `duplicates` instead of creating dupes

## Examinations (Exam Engine proxy + local history)

- `POST /api/v1/examinations/jobs` — submit a generation job to the Exam
  Engine; the platform records submission metadata only
  (job id, exam type, class, subject, timestamp)
- `GET /api/v1/examinations/jobs/{job_id}` — poll engine job status
- `GET /api/v1/examinations/history` — platform-side submission log

## Results (Results Engine proxy + local history)

- `GET /api/v1/results?scope=CAT-1|CAT-2|FINAL|CONSOLIDATED&class=…`
  — query the Results Engine; rows pass through untouched
- `GET /api/v1/results/history` — platform-side query metadata log
  (who asked, scope, class, timestamp — never the marks themselves)

## AI Gateway

- `GET /api/v1/ai/tasks` — allowed task names
- `GET /api/v1/ai/contexts` — allowed contexts per task
- `POST /api/v1/ai/run`:

```json
{
  "task": "draft_exam_outline | summarize_results | parent_message",
  "context": "CAT-1 | CAT-2 | FINAL | RESULTS | GENERAL",
  "input": "free text the model may quote, never invent beyond"
}
```

Task/context combinations outside the allow-list are rejected with 400
before any provider is contacted. A `missing_input` model refusal
surfaces immediately as a controlled error — it never fails over.

## Readiness

- `GET /api/v1/ready` — environment, database path, explicit `stub_mode`,
  real student counts, Exam/Results engine health, AI provider summary.
  Unreachable engines report `status: "unreachable"`; the platform still
  boots — engine outages are data, not crashes.
