"""Centralised, validated configuration.

Every setting comes from the environment (or .env in development).
Validation fails fast at startup so a misconfigured deploy never boots.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class AITier(BaseSettings):
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "staging", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_secret_key: str = Field(min_length=16)
    admin_api_token: str = Field(min_length=16)

    database_path: Path = BASE_DIR / "data" / "platform.db"

    exam_api_base_url: str = "http://127.0.0.1:8100"
    results_api_base_url: str = "http://127.0.0.1:8200"
    engine_api_timeout_seconds: int = Field(default=30, ge=1, le=300)
    engine_api_max_retries: int = Field(default=2, ge=0, le=5)

    ai_primary_provider: str = ""
    ai_primary_model: str = ""
    ai_primary_base_url: str = ""
    ai_primary_api_key: str = ""
    ai_secondary_provider: str = ""
    ai_secondary_model: str = ""
    ai_secondary_base_url: str = ""
    ai_secondary_api_key: str = ""
    ai_tertiary_provider: str = ""
    ai_tertiary_model: str = ""
    ai_tertiary_base_url: str = ""
    ai_tertiary_api_key: str = ""
    ai_timeout_seconds: int = Field(default=45, ge=5, le=300)
    ai_max_retries: int = Field(default=1, ge=0, le=3)
    ai_use_stub: bool = False

    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    upload_allowed_extensions: str = "xlsx,xls,pdf,png,jpg,jpeg"

    # Hard cap on any request body (413 beyond this).
    max_request_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("app_secret_key", "admin_api_token")
    @classmethod
    def _no_placeholder_secrets(cls, v: str) -> str:
        if "change-me" in v.lower():
            raise ValueError("placeholder secret detected — set a real value")
        return v

    @field_validator("exam_api_base_url", "results_api_base_url")
    @classmethod
    def _safe_service_url(cls, v: str) -> str:
        # SSRF gate: only http(s) or the explicit stub scheme. No userinfo,
        # no exotic schemes. Private-network filtering happens at deploy
        # time (engines are expected on the LAN); the scheme allowlist is
        # the code-level control.
        low = v.lower()
        if low.startswith("stub://"):
            return v
        if low.startswith(("http://", "https://")) and "@" not in v.split("://", 1)[1]:
            return v
        raise ValueError(f"unsafe service URL (must be http(s):// or stub://): {v!r}")

    @model_validator(mode="after")
    def _production_never_silent_stubs(self):
        if self.app_env != "production":
            return self
        stubs = [u for u in (self.exam_api_base_url, self.results_api_base_url)
                 if u.lower().startswith("stub://")]
        problems = []
        if stubs:
            problems.append(f"stub engine URLs in production: {stubs}")
        if self.ai_use_stub:
            problems.append("AI_USE_STUB=true in production")
        if problems:
            raise ValueError("production stub guard: " + "; ".join(problems)
                             + " — stubs must never run silently in production")
        return self

    @property
    def stub_mode(self) -> bool:
        """True when any stub double is active — always surfaced in UI/logs."""
        return (self.exam_api_base_url.lower().startswith("stub://")
                or self.results_api_base_url.lower().startswith("stub://")
                or self.ai_use_stub)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def ai_tiers(self) -> list[AITier]:
        tiers = [
            AITier(
                provider=self.ai_primary_provider,
                model=self.ai_primary_model,
                base_url=self.ai_primary_base_url,
                api_key=self.ai_primary_api_key,
            ),
            AITier(
                provider=self.ai_secondary_provider,
                model=self.ai_secondary_model,
                base_url=self.ai_secondary_base_url,
                api_key=self.ai_secondary_api_key,
            ),
            AITier(
                provider=self.ai_tertiary_provider,
                model=self.ai_tertiary_model,
                base_url=self.ai_tertiary_base_url,
                api_key=self.ai_tertiary_api_key,
            ),
        ]
        return [t for t in tiers if t.provider and t.base_url]

    @property
    def upload_allowed_extension_set(self) -> set[str]:
        return {e.strip().lower() for e in self.upload_allowed_extensions.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    # pydantic-settings fills required fields from the environment.
    return Settings()  # type: ignore[call-arg]
