"""SSRF / egress-filtering tests.

Proves the configuration layer rejects unsafe engine URLs:
- link-local / cloud metadata IPs are rejected unconditionally.
- private/loopback IPs require the explicit EGRESS_ALLOW_INTERNAL opt-in.
- scheme, userinfo, and port syntax are validated.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _s(**over) -> Settings:
    base = {
        "app_env": "development",
        "app_secret_key": "test-secret-key-0123456789abcdef",
        "admin_api_token": "test-admin-token-0123456789abcdef",
        "database_path": "/tmp/sap-ssrf.db",
        "exam_api_base_url": "stub://exam-engine",
        "results_api_base_url": "stub://results-engine",
        "ai_use_stub": True,
        "egress_allow_internal": True,
    }
    base.update(over)
    return Settings(**base)


# ── link-local / metadata: ALWAYS rejected, even with the allowlist ───

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254/",
    "http://169.254.0.1/",
    "https://169.254.1.1/",
])
def test_metadata_and_link_local_always_rejected(url):
    with pytest.raises(ValidationError, match="link-local"):
        _s(exam_api_base_url=url, egress_allow_internal=True)
    with pytest.raises(ValidationError, match="link-local"):
        _s(exam_api_base_url=url, egress_allow_internal=False)


# ── private / loopback: require the explicit allowlist ─────────────────

@pytest.mark.parametrize("url", [
    "http://10.0.0.5:8100",
    "http://192.168.1.10:8200",
    "http://127.0.0.1:8100",
    "http://0.0.0.0:8100",
])
def test_private_requires_explicit_allowlist(url):
    with pytest.raises(ValidationError, match="non-public"):
        _s(exam_api_base_url=url, egress_allow_internal=False)
    assert _s(exam_api_base_url=url, egress_allow_internal=True).egress_allow_internal


# ── syntax: scheme, userinfo, port ─────────────────────────────────────

@pytest.mark.parametrize("url", [
    "ftp://engines.local/exam",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http://user@10.0.0.5/exam",
    "http://engines.local:abc/exam",
])
def test_unsafe_url_syntax_rejected(url):
    with pytest.raises(ValidationError):
        _s(exam_api_base_url=url, egress_allow_internal=True)


def test_stub_scheme_bypasses_ip_checks():
    s = _s(exam_api_base_url="stub://exam-engine",
           results_api_base_url="stub://results-engine",
           egress_allow_internal=False)
    assert s.stub_mode


def test_unresolvable_public_hostname_passes_syntax_gate():
    """A hostname that does not resolve at boot is NOT rejected — the
    check is deferred to request time (fail-open on DNS failure is the
    documented behaviour). Only *resolved* private/loopback/link-local
    IPs are rejected. This test must not depend on external DNS."""
    s = _s(exam_api_base_url="http://engines.example.invalid:8100",
           results_api_base_url="https://engines.example.invalid:8200",
           egress_allow_internal=False)
    # ai_use_stub is True in the base fixture (offline test envelope);
    # the URL fields themselves passed validation without an allowlist.
    assert s.egress_allow_internal is False
    assert s.exam_api_base_url == "http://engines.example.invalid:8100"
