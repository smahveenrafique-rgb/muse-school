"""Shared fixtures: isolated app with temp database and stub engines."""

from __future__ import annotations

import os
import tempfile

# Set BEFORE importing app modules: app.main builds the app at import time
# and Settings fails fast without real secrets.
_tmp = tempfile.mkdtemp(prefix="sap-test-")
os.environ.setdefault("APP_SECRET_KEY", "import-time-secret-0123456789abcdef")
os.environ.setdefault("ADMIN_API_TOKEN", "import-time-token-0123456789abcdef")
os.environ.setdefault("DATABASE_PATH", os.path.join(_tmp, "import.db"))
os.environ.setdefault("EXAM_API_BASE_URL", "stub://exam-engine")
os.environ.setdefault("RESULTS_API_BASE_URL", "stub://results-engine")
os.environ.setdefault("EGRESS_ALLOW_INTERNAL", "true")

import pytest  # noqa: E402


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    """Run anyio-marked tests on asyncio only (trio is not a dependency)."""
    return request.param
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

TEST_TOKEN = "test-admin-token-0123456789abcdef"


@pytest.fixture()
def settings(tmp_path):
    s = Settings(
        app_env="development",
        app_secret_key="test-secret-key-0123456789abcdef",
        admin_api_token=TEST_TOKEN,
        database_path=tmp_path / "test.db",
        exam_api_base_url="stub://exam-engine",
        results_api_base_url="stub://results-engine",
        ai_use_stub=True,
        upload_max_bytes=1024 * 1024,
        egress_allow_internal=True,
    )
    # Generous login limit: tests share one source IP (testclient) and
    # exercise the full login flow repeatedly; the brute-force limit is
    # covered by a dedicated test that sets its own tight window.
    s.login_max_attempts = 100
    return s


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    fastapi_app = getattr(app, "user_app", app)  # unwrap the ASGI size cap
    fastapi_app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture()
def sample_student():
    return {
        "full_name": "Ali Raza",
        "father_name": "Muhammad Raza",
        "class_name": "5",
        "section": "A",
        "mobile": "03001234567",
    }
