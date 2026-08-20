"""Product orchestration around the canonical read-only MissionComposer.

This layer owns authentication, tenant authorization, database queueing, and
worker lifecycle. It intentionally does not duplicate the canonical mission
planner, provider semantics, verifier, or LocalStateStore checkpoint format.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4
import hashlib
import re
import time

from runtime.canonical_pilot import DirectGitHubAPIAdapter
from runtime.filesystem_provider import FilesystemReadProvider
from runtime.github_provider import GitHubReadProvider
from runtime.mission_composer import MissionComposer

from .config import ProductSettings
from .database import NexusDatabase
from .schemas import MissionSubmission


PROJECT_ROLE_RANK = {"viewer": 1, "operator": 2, "owner": 3}


class StandaloneMissionService:
    def __init__(self, settings: ProductSettings | None = None):
        self.settings = settings or ProductSettings.from_env()
        if self.settings.database_url and not self.settings.database_url.startswith("sqlite://"):
            raise ValueError("DATABASE_URL is configured for an unavailable engine; this product build currently executes only sqlite:// URLs")
        self.settings.ensure_directories()
        self.database = NexusDatabase(self.settings.database_path)
        self.database.migrate()
        self._bootstrap_from_settings()

    @staticmethod
    def _safe_project_id(project_id: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_-]", "-", project_id).strip("-").lower() or "local"

    @staticmethod
    def _public_identity(identity: dict[str, Any]) -> dict[str, Any]:
        return {key: identity[key] for key in ("user_id", "tenant_id", "email", "role") if key in identity}

    def _bootstrap_from_settings(self) -> None:
        email = self.settings.bootstrap_owner_email
        password = self.settings.bootstrap_owner_password
        if bool(email) != bool(password):
            raise ValueError("NEXUS_BOOTSTRAP_OWNER_EMAIL and NEXUS_BOOTSTRAP_OWNER_PASSWORD must be supplied together")
        if email and password:
            self.bootstrap_owner(email, password, self.settings.bootstrap_tenant_name, self.settings.bootstrap_project_id)

    def bootstrap_owner(self, email: str, password: str, tenant_name: str = "NEXUS", project_id: str = "local") -> dict[str, Any]:
        tenant = self.database.get_or_create_tenant(tenant_name)
        user = self.database.create_user(tenant["tenant_id"], email, password, role="owner")
        adopted = self.database.adopt_legacy_projects(tenant["tenant_id"])
        project = self.database.create_project(tenant["tenant_id"], self._safe_project_id(project_id), "Primary command center")
        self.database.grant_project_member(project["project_id"], user["user_id"], "owner")
        for legacy_project in adopted:
            self.database.grant_project_member(legacy_project, user["user_id"], "owner")
        return {"tenant": tenant, "user": self._public_identity(user), "project": project, "adopted_projects": adopted}

    def login(self, email: str, password: str) -> dict[str, Any] | None:
        user = self.database.authenticate_password(email, password)
        if not user:
            return None
        token, session = self.database.create_session(user["user_id"], self.settings.session_hours)
        self.database.add_audit_event(user["tenant_id"], user["user_id"], "auth.login", "success", {"session_id": session["session_id"]})
        return {"access_token": token, "token_type": "bearer", "expires_at": session["expires_at"], "user": self._public_identity(user), "projects": self.database.list_projects_for_user(user["user_id"], user["tenant_id"])}

    def authenticate_bearer(self, token: str) -> dict[str, Any] | None:
        identity = self.database.resolve_session(token)
        return self._public_identity(identity) if identity else None

    def logout(self, token: str) -> None:
        identity = self.database.resolve_session(token)
        self.database.revoke_session(token)
        if identity:
            self.database.add_audit_event(identity["tenant_id"], identity["user_id"], "auth.logout", "success", {"session_id": identity["session_id"]})

    def list_projects(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        return self.database.list_projects_for_user(principal["user_id"], principal["tenant_id"])

    def create_project(self, principal: dict[str, Any], project_id: str, display_name: str) -> dict[str, Any]:
        if principal["role"] != "owner":
            raise PermissionError("only a tenant owner can create a project")
        project = self.database.create_project(principal["tenant_id"], self._safe_project_id(project_id), display_name)
        self.database.grant_project_member(project["project_id"], principal["user_id"], "owner")
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "project.create", "success", {"display_name": project["display_name"]}, project_id=project["project_id"])
        return project

    def _require_project_role(self, principal: dict[str, Any], project_id: str, minimum: str = "viewer") -> str:
        role = self.database.project_role(principal["user_id"], principal["tenant_id"], self._safe_project_id(project_id))
        if not role or PROJECT_ROLE_RANK[role] < PROJECT_ROLE_RANK[minimum]:
            raise PermissionError("project membership does not permit this request")
        return role

    def _mission_for_principal(self, principal: dict[str, Any], mission_id: str, minimum: str = "viewer") -> dict[str, Any] | None:
        record = self.database.get_mission(mission_id)
        if record is None:
            return None
        if record.get("tenant_id") != principal["tenant_id"]:
            raise PermissionError("mission belongs to another tenant")
        self._require_project_role(principal, record["project_id"], minimum)
        return record

    def _store_root(self, project_id: str) -> Path:
        root = self.settings.state_root / self._safe_project_id(project_id)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _composer(self) -> MissionComposer:
        composer = MissionComposer()
        composer.providers["github-read"] = GitHubReadProvider(
            DirectGitHubAPIAdapter(
                token=self.settings.github_token,
                api_base=self.settings.github_api_base,
                timeout=self.settings.github_timeout_seconds,
            )
        )
        composer.provider = composer.providers["github-read"]
        composer.providers["filesystem-read"] = FilesystemReadProvider([self.settings.allowed_filesystem_root])
        return composer

    def enqueue_mission(self, principal: dict[str, Any], submission: MissionSubmission) -> dict[str, Any]:
        if submission.mode == "REAL_READ" and not self.settings.allow_real_reads:
            raise PermissionError("REAL_READ is disabled by standalone product configuration")
        project_id = self._safe_project_id(submission.project_id)
        self._require_project_role(principal, project_id, "operator")
        store_root = self._store_root(project_id)
        mission_id = f"mission-{uuid4()}"
        self.database.create_mission(
            mission_id=mission_id,
            tenant_id=principal["tenant_id"],
            project_id=project_id,
            scope=submission.scope,
            intent=submission.intent,
            mode=submission.mode,
            capabilities=submission.capabilities or [],
            store_root=str(store_root),
            submission=submission.model_dump(mode="json"),
            max_attempts=self.settings.queue_max_attempts,
        )
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.enqueue", "success", {"mode": submission.mode, "capabilities": submission.capabilities or []}, project_id=project_id, mission_id=mission_id)
        return self.get_mission(principal, mission_id, include_result=False) or {"mission_id": mission_id, "status": "QUEUED"}

    def _execute_record(self, record: dict[str, Any]) -> dict[str, Any]:
        submission = MissionSubmission.model_validate(record.get("submission") or {})
        composer = self._composer()
        package = composer.compose_capability_mission(
            submission.intent,
            scope=submission.scope,
            mode=submission.mode,
            browser_url=submission.browser_url or self.settings.browser_url,
            filesystem_path=submission.filesystem_path or str(self.settings.product_root / "README.md"),
            capabilities=submission.capabilities,
            store_root=record["store_root"],
            repository_scope=submission.repository_scope or self.settings.github_repository,
        )
        package["mission"]["mission_id"] = record["mission_id"]
        return composer.execute_capability_mission(package, record["store_root"], submission.mode)

    def worker_once(self, worker_id: str | None = None) -> dict[str, Any] | None:
        worker_id = worker_id or f"worker-{uuid4()}"
        self.database.heartbeat_worker(worker_id, "ACTIVE", {"queue": self.database.queue_health()})
        record = self.database.claim_next(worker_id, self.settings.worker_lease_seconds)
        if record is None:
            return None
        mission_id = record["mission_id"]
        try:
            result = self._execute_record(record)
            self.database.save_result(mission_id, result)
            checkpoint = Path(record["store_root"]) / "current.json"
            if checkpoint.exists():
                checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
                self.database.record_checkpoint(record["tenant_id"], record["project_id"], mission_id, str(checkpoint), checksum, result.get("mission", {}).get("state", "UNKNOWN"))
            self.database.finish_queue(mission_id, worker_id)
            return self.database.get_mission(mission_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.database.retry_or_fail_queue(mission_id, worker_id, error)
            return self.database.get_mission(mission_id)

    def run_worker(self, worker_id: str | None = None, once: bool = False) -> int:
        worker_id = worker_id or f"worker-{uuid4()}"
        processed = 0
        while True:
            result = self.worker_once(worker_id)
            if result:
                processed += 1
            if once:
                return processed
            if not result:
                self.database.heartbeat_worker(worker_id, "ACTIVE", {"queue": self.database.queue_health(), "idle": True})
                time.sleep(self.settings.worker_poll_seconds)

    def submit_and_execute(self, principal: dict[str, Any], submission: MissionSubmission) -> dict[str, Any]:
        """CLI-only compatibility helper; API clients must use the durable queue."""
        queued = self.enqueue_mission(principal, submission)
        self.worker_once("cli-inline-worker")
        return self.get_mission(principal, queued["mission_id"], include_result=True) or queued

    def get_mission(self, principal: dict[str, Any], mission_id: str, include_result: bool = False) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id)
        if record is None:
            return None
        if not include_result:
            record.pop("result", None)
        return record

    def list_missions(self, principal: dict[str, Any], project_id: str, limit: int = 30) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        records = self.database.list_missions(safe_project, limit)
        for record in records:
            record.pop("result", None)
        return records

    def mission_evidence(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        if not self._mission_for_principal(principal, mission_id):
            return []
        return self.database.evidence(mission_id)

    def mission_events(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        if not self._mission_for_principal(principal, mission_id):
            return []
        return self.database.events(mission_id)

    def recover(self, principal: dict[str, Any], mission_id: str) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id, "operator")
        if record is None:
            return None
        composer = self._composer()
        recovery = composer.recover(record["store_root"], record["scope"])
        self.database.add_event(mission_id, "standalone_recovery_checked", recovery)
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.recover", "success", {"recovery_status": recovery.get("status")}, project_id=record["project_id"], mission_id=mission_id)
        return {"mission": self.get_mission(principal, mission_id, include_result=False), "recovery": recovery}

    def continue_mission(self, principal: dict[str, Any], mission_id: str) -> dict[str, Any] | None:
        """Recover the persisted canonical state; never fabricate or re-run a completed mission."""
        recovered = self.recover(principal, mission_id)
        if recovered is None:
            return None
        mission = recovered["mission"]
        self.database.add_event(mission_id, "mission_continued", {"recovery_status": recovered["recovery"].get("status"), "execution_restarted": False})
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], "mission.continue", "success", {"recovery_status": recovered["recovery"].get("status")}, project_id=mission["project_id"], mission_id=mission_id)
        return recovered

    def control_mission(self, principal: dict[str, Any], mission_id: str, control: str) -> dict[str, Any] | None:
        record = self._mission_for_principal(principal, mission_id, "operator")
        if record is None:
            return None
        state = {"pause": "PAUSED", "resume": "QUEUED", "cancel": "CANCELLED"}.get(control)
        if state is None:
            raise ValueError("unknown mission control")
        if not self.database.control_mission(mission_id, state):
            raise ValueError("mission cannot be controlled while executing or after it is missing")
        self.database.add_audit_event(principal["tenant_id"], principal["user_id"], f"mission.{control}", "success", {"state": state}, project_id=record["project_id"], mission_id=mission_id)
        return self.get_mission(principal, mission_id, include_result=False)

    def list_memory(self, principal: dict[str, Any], project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.list_memory(principal["tenant_id"], safe_project, limit)

    def update_memory(self, principal: dict[str, Any], project_id: str, memory_id: str, action: str, note: str | None = None) -> dict[str, Any] | None:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "operator")
        record = self.database.set_memory_lifecycle(principal["tenant_id"], safe_project, memory_id, principal["user_id"], action, note)
        if record:
            self.database.add_audit_event(principal["tenant_id"], principal["user_id"], f"memory.{action}", "success", {"memory_id": memory_id, "note_present": bool(note)}, project_id=safe_project)
        return record

    def project_context(self, principal: dict[str, Any], project_id: str) -> dict[str, Any]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.project_context(principal["tenant_id"], safe_project)

    def list_outcomes(self, principal: dict[str, Any], project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_project = self._safe_project_id(project_id)
        self._require_project_role(principal, safe_project, "viewer")
        return self.database.list_outcomes(principal["tenant_id"], safe_project, limit)

    def list_audit_events(self, principal: dict[str, Any], project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if project_id:
            self._require_project_role(principal, self._safe_project_id(project_id), "viewer")
        return self.database.list_audit_events(principal["tenant_id"], project_id, limit)

    def mission_checkpoints(self, principal: dict[str, Any], mission_id: str) -> list[dict[str, Any]]:
        record = self._mission_for_principal(principal, mission_id, "viewer")
        if record is None:
            return []
        return self.database.list_checkpoints(principal["tenant_id"], mission_id)

    def capabilities(self, principal: dict[str, Any]) -> list[dict[str, Any]]:
        provider_state = self.providers(principal)
        contracts = [
            ("repository.metadata.read", "github-read", "read-only"),
            ("repository.read", "github-read", "read-only"),
            ("browser.read", "browser-read", "read-only"),
            ("filesystem.read", "filesystem-read", "bounded-read-only"),
        ]
        return [{"capability": capability, "provider": provider, "risk": risk, "side_effects": False, "status": provider_state.get(provider, {}).get("status", "UNAVAILABLE"), "availability": bool(provider_state.get(provider, {}).get("availability")), "authorization": "READ_ONLY_AUTHORIZED" if provider_state.get(provider, {}).get("availability") else "NOT_AVAILABLE"} for capability, provider, risk in contracts]

    def providers(self, principal: dict[str, Any]) -> dict[str, Any]:
        history = self.database.provider_history(principal["tenant_id"])
        composer = self._composer()
        providers: dict[str, Any] = {}
        for name, provider in composer.providers.items():
            if not hasattr(provider, "health"):
                continue
            current = provider.health()
            providers[name] = {**current, "identity": name, "authorization": "READ_ONLY_AUTHORIZED" if current.get("availability") else "NOT_AVAILABLE", "risk": "LOW_READ_ONLY", "side_effects": False, "execution_state": "EXECUTED" if history.get(name, {}).get("last_execution") else "NOT_EXECUTED", **history.get(name, {})}
        return providers

    def database_inspection(self, principal: dict[str, Any]) -> dict[str, Any]:
        if principal["role"] != "owner":
            raise PermissionError("only a tenant owner can inspect product database facts")
        return self.database.tenant_inspection(principal["tenant_id"])

    def health(self) -> dict[str, Any]:
        composer = self._composer()
        provider_health = {name: provider.health() for name, provider in composer.providers.items() if hasattr(provider, "health")}
        database = self.database.health()
        queue = database["queue"]
        workers = database["workers"]
        if database["users"] == 0:
            lifecycle = "REQUIRES_ATTENTION"
            reason = "No product owner exists. Bootstrap an owner before mission access is possible."
        elif queue["failed"]:
            lifecycle = "REQUIRES_ATTENTION"
            reason = "One or more missions are in a durable failed state and require operator review."
        elif (queue["queued"] or queue["leased"]) and workers["active_count"] == 0:
            lifecycle = "DEGRADED"
            reason = "Durable mission work is waiting but no active worker heartbeat is present."
        elif queue["leased"]:
            lifecycle = "RECOVERING"
            reason = "A worker holds an active lease; execution or recovery is in progress."
        else:
            lifecycle = "READY"
            reason = "Database is healthy and no blocked durable mission requires action."
        return {
            "service": "nexus-independent",
            "status": lifecycle,
            "runtime_state": {"state": lifecycle, "reason": reason, "configured_database_url": self.settings.database_url or f"sqlite:///{self.settings.database_path}", "database_engine": "sqlite", "database_portability": "SQLITE_EXECUTED__POSTGRESQL_UNAVAILABLE"},
            "database": database,
            "providers": provider_health,
            "real_reads_enabled": self.settings.allow_real_reads,
            "authorization_boundary": "authenticated tenant members may invoke read-only providers only; consequential operations are not exposed",
            "authentication": {"mode": "product-owned-session-bearer", "bootstrap_owner_configured": bool(self.settings.bootstrap_owner_email and self.settings.bootstrap_owner_password), "session_hours": self.settings.session_hours},
            "queue": {"worker_command": "nexus-independent worker", "lease_seconds": self.settings.worker_lease_seconds, "max_attempts": self.settings.queue_max_attempts, "worker_health": workers},
            "github": {"transport": "direct-github-rest", "authentication": "PRODUCT_MANAGED_TOKEN" if self.settings.github_token else "PUBLIC_READ_ONLY"},
        }

    def public_health(self) -> dict[str, Any]:
        """Return readiness only; tenant and host details require product authentication."""
        private = self.health()
        return {
            "service": "nexus-independent",
            "status": private["status"],
            "database": {"status": "HEALTHY"},
            "authorization_boundary": "product authentication and tenant membership are required for mission data",
        }

    def diagnostics(self, principal: dict[str, Any]) -> dict[str, Any]:
        del principal
        return self.health()
