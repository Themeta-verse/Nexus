"""Explicit configuration for the independently owned NEXUS product runtime.

No setting is sourced from development tooling. Credentials remain process-local
environment variables and are never persisted in the product database.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(value: str | None, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(value) if value is not None else default
    except ValueError:
        return default
    return max(minimum, parsed)


@dataclass(frozen=True)
class ProductSettings:
    product_root: Path
    database_path: Path
    state_root: Path
    allowed_filesystem_root: Path
    github_repository: str
    browser_url: str
    allow_real_reads: bool
    api_host: str
    api_port: int
    web_origins: tuple[str, ...]
    github_api_base: str = "https://api.github.com"
    github_token: str | None = field(default=None, repr=False)
    github_timeout_seconds: int = 20
    worker_lease_seconds: int = 90
    worker_poll_seconds: int = 2
    queue_max_attempts: int = 3
    session_hours: int = 12
    bootstrap_owner_email: str | None = None
    bootstrap_owner_password: str | None = field(default=None, repr=False)
    bootstrap_tenant_name: str = "NEXUS"
    bootstrap_project_id: str = "local"

    @classmethod
    def from_env(cls) -> "ProductSettings":
        product_root = Path(os.getenv("NEXUS_PRODUCT_ROOT", Path(__file__).resolve().parents[1])).resolve()
        data_root = Path(os.getenv("NEXUS_DATA_ROOT", product_root / ".nexus_product")).resolve()
        database_path = Path(os.getenv("NEXUS_DATABASE_PATH", data_root / "nexus.db")).resolve()
        state_root = Path(os.getenv("NEXUS_STATE_ROOT", data_root / "state")).resolve()
        allowed_root = Path(os.getenv("NEXUS_ALLOWED_FILESYSTEM_ROOT", product_root)).resolve()
        return cls(
            product_root=product_root,
            database_path=database_path,
            state_root=state_root,
            allowed_filesystem_root=allowed_root,
            github_repository=os.getenv("NEXUS_GITHUB_REPOSITORY", "Themeta-verse/Nexus"),
            browser_url=os.getenv("NEXUS_BROWSER_URL", "https://github.com/Themeta-verse/Nexus"),
            allow_real_reads=_bool(os.getenv("NEXUS_ALLOW_REAL_READS"), True),
            api_host=os.getenv("NEXUS_API_HOST", "127.0.0.1"),
            api_port=_positive_int(os.getenv("NEXUS_API_PORT"), 8787),
            web_origins=tuple(origin.strip() for origin in os.getenv("NEXUS_WEB_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()),
            github_api_base=os.getenv("NEXUS_GITHUB_API_BASE", "https://api.github.com").rstrip("/"),
            github_token=os.getenv("NEXUS_GITHUB_TOKEN") or None,
            github_timeout_seconds=_positive_int(os.getenv("NEXUS_GITHUB_TIMEOUT_SECONDS"), 20),
            worker_lease_seconds=_positive_int(os.getenv("NEXUS_WORKER_LEASE_SECONDS"), 90, minimum=15),
            worker_poll_seconds=_positive_int(os.getenv("NEXUS_WORKER_POLL_SECONDS"), 2),
            queue_max_attempts=_positive_int(os.getenv("NEXUS_QUEUE_MAX_ATTEMPTS"), 3),
            session_hours=_positive_int(os.getenv("NEXUS_SESSION_HOURS"), 12),
            bootstrap_owner_email=os.getenv("NEXUS_BOOTSTRAP_OWNER_EMAIL") or None,
            bootstrap_owner_password=os.getenv("NEXUS_BOOTSTRAP_OWNER_PASSWORD") or None,
            bootstrap_tenant_name=os.getenv("NEXUS_BOOTSTRAP_TENANT", "NEXUS"),
            bootstrap_project_id=os.getenv("NEXUS_BOOTSTRAP_PROJECT", "local"),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
