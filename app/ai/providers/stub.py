"""Deterministic stub provider — tests and offline development.

Returns canned, schema-valid JSON per task type. The behaviour script lets
tests force failures (timeout / rate-limit / malformed) to exercise the
failover matrix without any network.
"""

from __future__ import annotations

from app.ai.providers.base import AIProvider, ChatMessage, ProviderResult


class StubProvider(AIProvider):
    name = "stub"

    def __init__(self, *, script: dict | None = None, **kwargs):
        # kwargs accepted for a uniform constructor; stub ignores credentials.
        super().__init__(model=kwargs.get("model", "stub-model"),
                         base_url=kwargs.get("base_url", "stub://ai"),
                         api_key="", timeout=kwargs.get("timeout", 45))
        self.script = script or {}

    def complete(self, messages: list[ChatMessage], *, request_id: str) -> ProviderResult:
        forced = self.script.get("force")
        if forced == "timeout":
            return ProviderResult(ok=False, provider="stub", model=self.model,
                                  latency_ms=1.0, error_kind="timeout",
                                  error_detail="scripted timeout")
        if forced == "rate_limited":
            return ProviderResult(ok=False, provider="stub", model=self.model,
                                  latency_ms=1.0, error_kind="rate_limited",
                                  error_detail="scripted 429")
        if forced == "malformed":
            return ProviderResult(ok=True, provider="stub", model=self.model,
                                  latency_ms=1.0, text="NOT JSON {{{")
        task = self.script.get("task", "draft_exam_outline")
        return ProviderResult(ok=True, provider="stub", model=self.model,
                              latency_ms=1.0, text=_canned(task))


def _canned(task: str) -> str:
    if task == "summarize_results":
        return ('{"summary": "Stub summary of the provided result rows.", '
                '"highlights": ["Stub highlight one."], "concerns": []}')
    if task == "parent_message":
        return ('{"message": "Assalam-o-Alaikum, this is a stub parent message.", '
                '"tone": "formal"}')
    return ('{"title": "Stub outline", "sections": '
            '[{"heading": "Stub section", "points": ["Stub point"]}], '
            '"notes": "Stub notes."}')
