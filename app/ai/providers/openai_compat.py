"""OpenAI-compatible chat-completions adapter (async).

One adapter covers OpenRouter, Groq, Gemini (via its OpenAI-compatible
endpoint), and any other OpenAI-compatible provider — the difference is
pure configuration (base URL + model + key), never code.

Concurrency: native async over a shared `httpx.AsyncClient` pool. The
shared client is attached by the gateway at construction time; when
absent (standalone use/tests), a private pooled client is created and
owned by this provider instance.
"""

from __future__ import annotations

import time

import httpx

from app.ai.providers.base import AIProvider, ChatMessage, ProviderResult

# HTTP statuses that mean "try the next provider".
_RATE_LIMITED = {408, 425, 429}
_QUOTA = {402, 403}

_POOL_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=50)


class OpenAICompatProvider(AIProvider):
    name = "openai_compat"

    def __init__(self, *, provider_label: str, pool: httpx.AsyncClient | None = None,
                 **kwargs):
        super().__init__(**kwargs)
        self.provider_label = provider_label  # e.g. "openrouter" — for logs
        self._owns_pool = pool is None
        if self._owns_pool:
            self._pool = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5.0, pool=5.0),
                trust_env=False,
                limits=_POOL_LIMITS,
            )
        else:
            self._pool = pool

    async def a_complete(self, messages: list[ChatMessage], *,
                         request_id: str) -> ProviderResult:
        t0 = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
        }
        try:
            # trust_env=False: provider routing is fully determined by
            # configuration, never by the operator's shell proxy variables.
            resp = await self._pool.post(f"{self.base_url}/chat/completions",
                                          json=payload, headers=headers)
        except httpx.TimeoutException:
            return self._fail("timeout", "request timed out", t0)
        except httpx.NetworkError as exc:
            return self._fail("transport_error",
                              f"network error: {type(exc).__name__}", t0)
        except httpx.HTTPError as exc:
            return self._fail("transport_error",
                              f"http error: {type(exc).__name__}", t0)

        latency = (time.perf_counter() - t0) * 1000
        if resp.status_code in _RATE_LIMITED:
            return self._fail("rate_limited", f"http {resp.status_code}", t0, latency)
        if resp.status_code in _QUOTA:
            return self._fail("quota_exceeded", f"http {resp.status_code}", t0, latency)
        if resp.status_code >= 400:
            # 4xx is deterministic: no body echo to the caller.
            return self._fail("bad_response", f"http {resp.status_code}", t0, latency)
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return self._fail("bad_response",
                              f"unparseable provider envelope: {exc}", t0, latency)
        if not text.strip():
            return self._fail("bad_response", "empty completion", t0, latency)
        return ProviderResult(ok=True, text=text, provider=self.provider_label,
                              model=self.model, latency_ms=latency)

    async def aclose(self) -> None:
        """Close the private pool (no-op when a shared pool is attached)."""
        if self._owns_pool and self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.aclose()

    def _fail(self, kind: str, detail: str, t0: float,
               latency: float | None = None) -> ProviderResult:
        return ProviderResult(
            ok=False, provider=self.provider_label, model=self.model,
            latency_ms=latency if latency is not None else (time.perf_counter() - t0) * 1000,
            error_kind=kind, error_detail=detail[:200],
        )
