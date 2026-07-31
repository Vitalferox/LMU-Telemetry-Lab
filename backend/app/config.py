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
    # Comma-separated bearer tokens, each optionally bound to a profile:
    #   "thierry:tok_abc,paul:tok_def"  → token identifies both the user and their profile
    #   "tok_abc"                       → unbound token (no profile lock)
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

    def _parsed_tokens(self) -> list[tuple[str | None, str]]:
        """Split AUTH_TOKENS into (profile_id | None, token) pairs."""
        pairs: list[tuple[str | None, str]] = []
        for entry in self.AUTH_TOKENS.split(","):
            entry = entry.strip()
            if not entry:
                continue
            profile, sep, token = entry.partition(":")
            if sep and profile.strip() and token.strip():
                pairs.append((profile.strip(), token.strip()))
            else:
                pairs.append((None, entry))
        return pairs

    @property
    def auth_token_set(self) -> set[str]:
        return {token for _, token in self._parsed_tokens()}

    @property
    def token_to_profile(self) -> dict[str, str]:
        """Tokens bound to a profile. Unbound tokens are absent from this map."""
        return {token: profile for profile, token in self._parsed_tokens() if profile}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
