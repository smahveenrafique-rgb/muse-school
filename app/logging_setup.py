"""Structured JSON logging with request correlation IDs.

Never log: API keys, credentials, full AI payloads, or unnecessary
student PII. The redactor below is the single choke point — add new
sensitive keys here, not at call sites.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

SENSITIVE_KEYS = {
    "api_key", "apikey", "authorization", "token", "secret", "password",
    "passwd", "credential", "private_key", "client_secret",
}

_SENSITIVE_RE = re.compile(
    r"(" + "|".join(sorted(SENSITIVE_KEYS, key=len, reverse=True)) + r")",
    re.IGNORECASE,
)


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex[:16]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def redact(obj):
    """Recursively mask sensitive values. Safe on any JSON-ish structure."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if _SENSITIVE_RE.search(str(k)) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) > 0 and _SENSITIVE_RE.search(obj):
        return "***REDACTED***"
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": get_request_id(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy libs; our own loggers keep the configured level.
    for noisy in ("uvicorn.access", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("school-ai-platform")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"school-ai-platform.{name}")


def log_event(logger: logging.Logger, level: int, msg: str, **fields) -> None:
    logger.log(level, msg, extra={"extra_fields": fields})
