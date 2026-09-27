"""AI Gateway tests: failover matrix, validation, prompt isolation."""

from __future__ import annotations

import pytest

from app.ai.gateway import AIGateway, AIServiceError
from app.ai.prompts import allowed_tasks, build_system_prompt
from app.ai.providers.stub import StubProvider


def make_gateway(*scripts, settings):
    providers = [StubProvider(script=s) for s in scripts]
    return AIGateway(settings, provider_overrides=providers)


def test_primary_success_no_failover(settings):
    gw = make_gateway({"task": "draft_exam_outline"}, settings=settings)
    res = gw.run(task="draft_exam_outline", context="CAT-1",
                 user_input="Outline for class 5 mathematics.", request_id="t1")
    assert res.ok and res.failovers == 0
    assert res.data["title"] == "Stub outline"
    assert res.provider == "stub"


def test_primary_timeout_fails_over_to_secondary(settings):
    gw = make_gateway({"force": "timeout", "task": "draft_exam_outline"},
                      {"task": "summarize_results"}, settings=settings)
    res = gw.run(task="summarize_results", context="CAT-2",
                 user_input="rows…", request_id="t2")
    assert res.ok and res.failovers == 1
    assert "summary" in res.data


def test_tertiary_catches_after_two_failures(settings):
    gw = make_gateway({"force": "timeout"}, {"force": "rate_limited"},
                      {"task": "parent_message"}, settings=settings)
    res = gw.run(task="parent_message", context="FINAL",
                 user_input="Result: 80%.", request_id="t3")
    assert res.ok and res.failovers == 2
    assert "message" in res.data


def test_all_providers_fail_is_controlled_error(settings):
    gw = make_gateway({"force": "timeout"}, {"force": "rate_limited"},
                      settings=settings)
    res = gw.run(task="draft_exam_outline", context="CAT-1",
                 user_input="x", request_id="t4")
    assert not res.ok
    assert res.failovers == 1  # two providers → one failover event
    assert "All AI providers failed" in res.error


def test_malformed_output_fails_over(settings):
    gw = make_gateway({"force": "malformed"}, {"task": "draft_exam_outline"},
                      settings=settings)
    res = gw.run(task="draft_exam_outline", context="CAT-1",
                 user_input="x", request_id="t5")
    assert res.ok and res.failovers == 1  # never silently accepted


def test_schema_violation_fails_over(settings):
    class BadJson(StubProvider):
        def complete(self, messages, *, request_id):
            from app.ai.providers.base import ProviderResult
            return ProviderResult(ok=True, provider="stub", model="stub",
                                  latency_ms=1.0, text='{"title": 123}')
    gw = AIGateway(settings, provider_overrides=[BadJson(), StubProvider()])
    res = gw.run(task="draft_exam_outline", context="CAT-1",
                 user_input="x", request_id="t6")
    assert res.ok and res.failovers == 1


def test_missing_input_refusal_is_controlled(settings):
    class Refuser(StubProvider):
        def complete(self, messages, *, request_id):
            from app.ai.providers.base import ProviderResult
            return ProviderResult(ok=True, provider="stub", model="stub",
                                  latency_ms=1.0,
                                  text='{"error": "missing_input", "needed": ["subject"]}')
    gw = AIGateway(settings, provider_overrides=[Refuser(), StubProvider()])
    res = gw.run(task="draft_exam_outline", context="CAT-1",
                 user_input="x", request_id="t7")
    # A missing-input refusal surfaces immediately as a controlled error —
    # it must NOT fail over to a provider that might invent the data.
    assert not res.ok and res.failovers == 0
    assert "missing input" in res.error


def test_no_providers_configured_raises(settings):
    gw = AIGateway(settings, provider_overrides=[])
    with pytest.raises(AIServiceError):
        gw.run(task="draft_exam_outline", context="CAT-1",
               user_input="x", request_id="t8")


def test_unknown_task_rejected(settings):
    gw = make_gateway({}, settings=settings)
    with pytest.raises(ValueError):
        gw.run(task="write_essay", context="CAT-1", user_input="x", request_id="t9")


# ── prompt routing ──────────────────────────────────────────────────────

def test_prompts_are_isolated_per_exam_type():
    cat1 = build_system_prompt("draft_exam_outline", "CAT-1")
    cat2 = build_system_prompt("draft_exam_outline", "CAT-2")
    final = build_system_prompt("draft_exam_outline", "FINAL")
    assert "CAT-1" in cat1 and "CAT-2" not in cat1 and "FINAL" not in cat1
    assert "CAT-2" in cat2 and "CAT-1" not in cat2
    assert "FINAL" in final and "CAT-1" not in final


def test_prompts_forbid_business_rule_invention():
    for task in allowed_tasks():
        p = build_system_prompt(task, "CAT-1")
        assert "NEVER invent" in p
        assert "single valid JSON object" in p


def test_unknown_exam_type_rejected():
    with pytest.raises(ValueError):
        build_system_prompt("draft_exam_outline", "MIDTERM")


# ── API surface ─────────────────────────────────────────────────────────

def test_ai_tasks_listed(client):
    assert set(client.get("/api/v1/ai/tasks").json()) == set(allowed_tasks())


def test_ai_run_via_api_uses_stub(client, auth_headers):
    r = client.post("/api/v1/ai/run", headers=auth_headers, json={
        "task": "parent_message", "context": "CAT-1",
        "input": "Student scored 20/25 in Mathematics.",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["provider"] == "stub"
    assert "message" in body["data"]


def test_ai_run_requires_auth(client):
    r = client.post("/api/v1/ai/run", json={
        "task": "parent_message", "context": "CAT-1", "input": "x"})
    assert r.status_code == 401
