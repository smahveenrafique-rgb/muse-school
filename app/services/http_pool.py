"""Shared async HTTP pool for outbound engine/provider traffic.

One pooled `httpx.AsyncClient` per app instance (created in
`create_app`, released by the lifespan shutdown hook). The pool is
transport-only (no base URL): each engine/provider applies its own
base URL to absolute request URLs, so a single pool serves all
outbound destinations with connection reuse.

Standalone callers (tests, scripts) without an app get a lazily
created private pool; `close_http_pool` releases it.
"""

from __future__ import annotations

import httpx

from app.config import Settings


def resolve_http_pool(settings: Settings) -> httpx.AsyncClient:
    """Return the app-level pool, lazily creating a private one on first
    use. `settings._http_pool` is set by `create_app`; the fallback here
    allows standalone use (tests, scripts) without an app. Callers that
    invoke `resolve_http_pool` without an app own the private pool and
    should call `close_http_pool` to release it."""
    pool: httpx.AsyncClient | None = getattr(settings, "_http_pool", None)
    if pool is None:
        pool = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.engine_api_timeout_seconds,
                                  connect=5.0, pool=5.0),
            trust_env=False,
            limits=httpx.Limits(max_keepalive_connections=20,
                                max_connections=50),
        )
        settings._http_pool = pool  # type: ignore[attr-defined]
    return pool


async def close_http_pool(settings: Settings) -> None:
    pool: httpx.AsyncClient | None = getattr(settings, "_http_pool", None)
    if pool is not None:
        await pool.aclose()
        settings._http_pool = None  # type: ignore[attr-defined]
