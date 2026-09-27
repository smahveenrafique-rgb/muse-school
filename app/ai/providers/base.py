"""Provider abstraction for the AI Gateway.

Every provider speaks through this interface. Adding a new provider means
implementing one class — the gateway, prompts, and validation never change.

Concurrency: providers are async. `acreate` is the default async
implementation; sync-only tests may subclass and override `complete` —
the gateway detects which one is implemented and calls the sync path via
`asyncio.to_thread` so sync providers remain usable from tests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ProviderResult:
    ok: bool
    text: str = ""                    # raw model output
    provider: str = ""
    model: str = ""
    latency_ms: float = 0.0
    # Machine-readable failure class for the failover policy:
    #   timeout | rate_limited | quota_exceeded | bad_response | transport_error
    error_kind: str = ""
    error_detail: str = ""            # short, safe to log (never includes keys)


@dataclass
class ChatMessage:
    role: str   # "system" | "user"
    content: str


class AIProvider:
    """Base class — subclass and implement `acreate` (or `complete`)."""

    name: str = "base"

    def __init__(self, *, model: str, base_url: str, api_key: str, timeout: int):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def a_complete(self, messages: list[ChatMessage], *,
                         request_id: str) -> ProviderResult:
        """Async completion. Default implementation runs the sync `complete`
        in a worker thread so sync-only test providers keep working."""
        return await asyncio.to_thread(self.complete, messages, request_id=request_id)

    def complete(self, messages: list[ChatMessage], *, request_id: str) -> ProviderResult:
        """Sync fallback (thread-pool). Real providers should override
        `a_complete` with a native async implementation instead."""
        raise NotImplementedError

    def describe(self) -> dict:
        # Never include the key.
        return {"provider": self.name, "model": self.model, "base_url": self.base_url}
