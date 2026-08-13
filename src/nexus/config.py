from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when runtime configuration is unsafe or invalid."""


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    environment: str
    database_path: Path
    session_ttl_seconds: int
    session_pepper: str
    max_body_bytes: int
    login_rate_limit: int
    login_rate_window_seconds: int
    generated_ephemeral_pepper: bool

    @classmethod
    def from_environment(cls) -> Settings:
        environment = os.environ.get("NEXUS_ENVIRONMENT", "development").strip().lower()
        if environment not in {"development", "test", "staging", "production"}:
            raise ConfigurationError(
                "NEXUS_ENVIRONMENT must be development, test, staging, or production"
            )

        configured_pepper = os.environ.get("NEXUS_SESSION_PEPPER", "").strip()
        if environment == "production" and len(configured_pepper) < 32:
            raise ConfigurationError(
                "NEXUS_SESSION_PEPPER must be at least 32 characters in production"
            )
        generated_ephemeral_pepper = not bool(configured_pepper)
        session_pepper = configured_pepper or secrets.token_urlsafe(32)

        database_path = Path(os.environ.get("NEXUS_DATABASE_PATH", "data/nexus.sqlite3"))
        if database_path.is_dir():
            raise ConfigurationError("NEXUS_DATABASE_PATH must be a file path")

        return cls(
            app_name=os.environ.get("NEXUS_APP_NAME", "Nexus ML Laboratory"),
            environment=environment,
            database_path=database_path,
            session_ttl_seconds=_positive_int("NEXUS_SESSION_TTL_SECONDS", 3600),
            session_pepper=session_pepper,
            max_body_bytes=_positive_int("NEXUS_MAX_BODY_BYTES", 1_000_000),
            login_rate_limit=_positive_int("NEXUS_LOGIN_RATE_LIMIT", 10),
            login_rate_window_seconds=_positive_int("NEXUS_LOGIN_RATE_WINDOW_SECONDS", 60),
            generated_ephemeral_pepper=generated_ephemeral_pepper,
        )
