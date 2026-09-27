"""ASGI-level streaming request-body cap.

The HTTP middleware checks `Content-Length` for regular requests, but
`Transfer-Encoding: chunked` bodies carry no `Content-Length` and would
slip past that gate. This pure-ASGI middleware enforces
`MAX_REQUEST_BYTES` on the byte stream itself: bytes are counted as they
arrive and the response is short-circuited to 413 once the cap is
exceeded. It wraps the entire app, so every route — including file
uploads — is bounded.

`install_size_limit` attaches the wrapper to the FastAPI instance and
returns it; the wrapper exposes `user_app` so tooling (tests,
dependency overrides) can reach the underlying app.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class StreamingSizeLimitMiddleware:
    """Count streamed request bytes; abort with 413 past the cap.

    Implemented at the ASGI layer (not HTTP middleware) so chunked
    bodies are bounded even without a `Content-Length`. The cap is
    checked on every `http.request` message; once exceeded, the send
    side is short-circuited to a 413 and the receive side drains the
    remainder without forwarding further bytes to the app.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes
        # Expose the underlying app so tooling (tests, dependency
        # overrides) can reach it through the ASGI wrapper.
        self.user_app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await self._bound_http(scope, receive, send)

    async def _bound_http(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI flow for HTTP: stream-count the body, 413 past the cap."""
        state = {"received": 0, "aborted": False, "responded": False}

        async def size_limited_receive() -> Message:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return message
                if message["type"] != "http.request":
                    return message
                state["received"] += len(message.get("body", b"") or b"")
                if state["received"] > self.max_bytes:
                    state["aborted"] = True
                # Aborted: drain the rest of the body without forwarding;
                # the send side replies 413.
                if not message.get("more_body") or not state["aborted"]:
                    return message

        async def guarded_send(message: Message) -> None:
            if state["aborted"] and not state["responded"]:
                state["responded"] = True
                await send({
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", b"58"),
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"detail":"Request body too large"}',
                    "more_body": False,
                })
                return
            await send(message)

        await self.app(scope, size_limited_receive, guarded_send)


def install_size_limit(app: ASGIApp, max_bytes: int) -> StreamingSizeLimitMiddleware:
    """Wrap the ASGI app with the streaming size cap.

    Returns the wrapper (callable ASGI app). The underlying FastAPI is
    available via `wrapper.user_app` for dependency overrides / tests.
    """
    return StreamingSizeLimitMiddleware(app, max_bytes)
