"""Logging: sensitive values are redacted, never emitted."""

from __future__ import annotations

from app.logging_setup import redact


def test_redact_masks_sensitive_keys():
    payload = {
        "api_key": "sk-secret",
        "nested": {"Authorization": "Bearer x", "model": "gpt-x"},
        "items": [{"password": "p"}],
        "safe": "visible",
    }
    out = redact(payload)
    assert out["api_key"] == "***REDACTED***"
    assert out["nested"]["Authorization"] == "***REDACTED***"
    assert out["nested"]["model"] == "gpt-x"
    assert out["items"][0]["password"] == "***REDACTED***"
    assert out["safe"] == "visible"


def test_redact_handles_non_dicts():
    assert redact("plain") == "plain"
    assert redact(42) == 42
    assert redact(None) is None
