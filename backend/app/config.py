from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for parent in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
        candidate = os.path.join(parent, ".env")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.dirname(here)), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_find_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Deployment mode ---
    # "local"  = desktop app, no auth needed, dangerous endpoints enabled
    # "server" = hosted, auth required, dangerous endpoints disabled
    APP_MODE: str = "local"

    # --- Auth ---
    # Comma-separated bearer tokens. Each user gets their own.
    # Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    AUTH_TOKENS: str = ""

    # --- CORS ---
    # Comma-separated origins, e.g. "http://localhost:5173,https://telemetry.example.com"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"

    # --- AI Coach ---
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6-20250514"
    ANTHROPIC_MAX_TOKENS: int = 4096

    # --- Debug ---
    DEBUG: bool = False

    @property
    def is_server(self) -> bool:
        return self.APP_MODE.lower() == "server"

    @property
    def auth_token_set(self) -> set[str]:
        if not self.AUTH_TOKENS:
            return set()
        return {t.strip() for t in self.AUTH_TOKENS.split(",") if t.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
