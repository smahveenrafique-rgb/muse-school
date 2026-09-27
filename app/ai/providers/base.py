"""Provider abstraction for the AI Gateway.

Every provider speaks through this interface. Adding a new provider means
implementing one class — the gateway, prompts, and validation never change.
"""

from __future__ import annotations

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
    """Base class — subclass and implement `complete`."""

    name: str = "base"

    def __init__(self, *, model: str, base_url: str, api_key: str, timeout: int):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, messages: list[ChatMessage], *, request_id: str) -> ProviderResult:
        raise NotImplementedError

    def describe(self) -> dict:
        # Never include the key.
        return {"provider": self.name, "model": self.model, "base_url": self.base_url}
