"""AI Gateway — provider-agnostic orchestration with controlled failover.

Pipeline per request:
    task router → system prompt → provider chain → strict validation → caller

Failover policy:
    primary --(timeout/quota/rate-limit/transport/malformed)--> secondary
         --(same)--> tertiary --(same)--> controlled AI service error

Nothing is silently accepted: a malformed or schema-invalid response from
one provider fails over to the next; if every provider fails, the caller
gets a structured error — never invented data.

Concurrency: the chain is walked with `await`; per-tier backoff uses
`asyncio.sleep` with jitter — no event-loop blocking. Providers are
expected to implement `a_complete` (native async); sync-only providers
(test doubles) fall back to a thread-pool via the base class.

Logging (per attempt): provider, model, request_id, task, success/failure,
latency, failover event, validation result. Never: keys, full payloads.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field

from app.ai.prompts import allowed_tasks, build_system_prompt
from app.ai.providers.base import AIProvider, ChatMessage, ProviderResult
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.ai.providers.stub import StubProvider
from app.ai.schemas import validate_task_output
from app.config import Settings
from app.logging_setup import get_logger, log_event

log = get_logger("ai-gateway")

_FAILOVER_KINDS = {"timeout", "rate_limited", "quota_exceeded",
                   "transport_error", "bad_response", "validation_error"}

# Bounded backoff between retries of the same provider tier.
_RETRY_BASE_SECONDS = 0.2
_RETRY_JITTER_SECONDS = 0.1


@dataclass
class GatewayResult:
    ok: bool
    task: str
    data: dict = field(default_factory=dict)   # validated payload
    provider: str = ""
    model: str = ""
    attempts: int = 0
    failovers: int = 0
    error: str = ""


class AIServiceError(Exception):
    """Controlled error after the whole chain is exhausted."""


@dataclass
class _RunState:
    """Mutable per-run counters for the failover chain."""
    attempts: int = 0
    failovers: int = 0
    last_error: str = "unknown error"


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a completion (tolerates code fences).

    The JSON itself is then strictly validated — fence-stripping is a
    courtesy, not acceptance of malformed output.
    """
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.DOTALL)
    if m:
        t = m.group(1)
    try:
        obj = json.loads(t)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI output is not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("AI output JSON is not an object")
    return obj


class AIGateway:
    def __init__(self, settings: Settings,
                 provider_overrides: list[AIProvider] | None = None,
                 http_pool=None):
        self.settings = settings
        self._overrides = provider_overrides
        self._http_pool = http_pool

    def _build_chain(self, task: str = "") -> list[AIProvider]:
        if self._overrides is not None:
            return self._overrides
        if self.settings.ai_use_stub:
            return [StubProvider(script={"task": task})]
        chain: list[AIProvider] = []
        for tier in self.settings.ai_tiers:
            chain.append(OpenAICompatProvider(
                provider_label=tier.provider,
                model=tier.model, base_url=tier.base_url,
                api_key=tier.api_key, timeout=self.settings.ai_timeout_seconds,
                pool=self._http_pool,
            ))
        return chain

    def _validate_ok_result(self, task: str, result: ProviderResult,
                            request_id: str,
                            state: _RunState) -> GatewayResult | None:
        """Validate a successful provider response.

        Returns a terminal GatewayResult (success, or the controlled
        "missing input" refusal which must NOT fail over), or None to
        keep going down the chain.
        """
        try:
            parsed = _extract_json(result.text)
            validated = validate_task_output(task, parsed)
        except ValueError as exc:
            if "missing input" in str(exc):
                # Controlled model refusal — surface it directly. Failing
                # over here would risk another provider inventing the
                # missing data.
                log_event(log, 30, "ai missing input",
                          provider=result.provider, model=result.model,
                          request_id=request_id, task=task,
                          detail=str(exc)[:200])
                return GatewayResult(ok=False, task=task,
                                     attempts=state.attempts,
                                     failovers=state.failovers,
                                     error=f"AI needs more input: {exc}")
            state.last_error = f"validation_error: {exc}"
            return None
        log_event(log, 20, "ai success",
                  provider=result.provider, model=result.model,
                  request_id=request_id, task=task,
                  latency_ms=round(result.latency_ms, 1),
                  validation="pass",
                  output_chars=len(result.text))
        return GatewayResult(ok=True, task=task,
                             data=validated.model_dump(),
                             provider=result.provider,
                             model=result.model,
                             attempts=state.attempts,
                             failovers=state.failovers)

    async def _try_provider(self, provider: AIProvider, messages: list[ChatMessage],
                            task: str, request_id: str,
                            state: _RunState) -> GatewayResult | None:
        """Run one provider through its retries. Returns a terminal result
        (success or missing-input refusal), or None to fail over."""
        for retry in range(1 + self.settings.ai_max_retries):
            state.attempts += 1
            result = await provider.a_complete(messages, request_id=request_id)
            validation = "n/a"
            if result.ok:
                terminal = self._validate_ok_result(task, result, request_id,
                                                    state)
                if terminal is not None:
                    return terminal
                validation = "fail"
                result = ProviderResult(ok=False, provider=result.provider,
                                        model=result.model,
                                        error_kind="validation_error",
                                        error_detail=state.last_error[:200])
            else:
                state.last_error = f"{result.error_kind}: {result.error_detail}"
            log_event(log, 30, "ai attempt failed",
                      provider=result.provider, model=result.model,
                      request_id=request_id, task=task,
                      error_kind=result.error_kind,
                      error_detail=result.error_detail,
                      validation=validation,
                      latency_ms=round(result.latency_ms, 1),
                      retry=retry)
            if result.error_kind not in _FAILOVER_KINDS:
                break  # non-retryable for this provider; still fail over
            # Bounded backoff + jitter — no retry storms.
            delay = _RETRY_BASE_SECONDS * (2 ** retry) \
                + random.uniform(0, _RETRY_JITTER_SECONDS)
            await asyncio.sleep(delay)
        return None

    async def run(self, *, task: str, context: str,
                  user_input: str, request_id: str) -> GatewayResult:
        if task not in allowed_tasks():
            raise ValueError(f"unknown AI task: {task}")
        system_prompt = build_system_prompt(task, context)
        messages = [ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_input[:8000])]
        chain = self._build_chain(task)
        if not chain:
            raise AIServiceError("no AI providers configured")

        counter = _RunState()
        for i, provider in enumerate(chain):
            if i > 0:
                counter.failovers += 1
                log_event(log, 30, "ai failover",
                          task=task, request_id=request_id,
                          from_provider=chain[i - 1].describe()["provider"],
                          to_provider=provider.describe()["provider"],
                          reason=counter.last_error)
            terminal = await self._try_provider(provider, messages, task,
                                                request_id, counter)
            if terminal is not None:
                return terminal
        log_event(log, 40, "ai chain exhausted",
                  request_id=request_id, task=task,
                  attempts=counter.attempts,
                  failovers=counter.failovers,
                  last_error=counter.last_error)
        return GatewayResult(ok=False, task=task,
                             attempts=counter.attempts,
                             failovers=counter.failovers,
                             error=f"All AI providers failed: {counter.last_error}")

    async def aclose(self) -> None:
        """Release private provider pools (shared pool is app-level)."""
        for provider in self._build_chain():
            closer = getattr(provider, "aclose", None)
            if closer is not None:
                await closer()

    def sync_run(self, *, task: str, context: str,
                 user_input: str, request_id: str) -> GatewayResult:
        """Synchronous facade for tests, scripts, and sync-only callers.

        Runs the async pipeline in a private event loop. Inside a
        running loop (e.g. an async test) call `await gateway.run(...)`
        instead — `sync_run` is not re-entrant.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.run(task=task, context=context,
                         user_input=user_input, request_id=request_id))
        raise RuntimeError(
            "sync_run called from inside a running event loop — "
            "use `await gateway.run(...)` instead")
