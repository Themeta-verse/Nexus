"""Durable SQLite repository for product-owned NEXUS state.

The database owns tenancy, sessions, projects, queue state, and product records.
`runtime.MissionComposer` remains the only planner, executor, verifier, and
LocalStateStore checkpoint author.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
import base64
import hashlib
import json
import secrets
import shutil
import sqlite3
import uuid


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _safe_slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return cleaned[:100] or "nexus"


def _hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("bootstrap and product passwords must contain at least 12 characters")
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return "pbkdf2_sha256$600000$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


class NexusDatabase:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def migrate(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('owner','operator','viewer')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id, expires_at);
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    display_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_memberships (
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('owner','operator','viewer')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    scope TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    requested_capabilities_json TEXT NOT NULL,
                    submission_json TEXT,
                    status TEXT NOT NULL,
                    reality TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    action_state TEXT NOT NULL,
                    external_invocations INTEGER NOT NULL DEFAULT 0,
                    store_root TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS missions_project_created_idx ON missions(project_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS mission_queue (
                    mission_id TEXT PRIMARY KEY REFERENCES missions(mission_id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED','LEASED','COMPLETED','FAILED')),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_queue_claim_idx ON mission_queue(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS mission_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_events_mission_idx ON mission_events(mission_id, event_id);
                CREATE TABLE IF NOT EXISTS mission_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    capability TEXT,
                    provider TEXT,
                    observation_id TEXT,
                    verification_state TEXT,
                    reality TEXT,
                    receipt_json TEXT,
                    observation_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS mission_evidence_mission_idx ON mission_evidence(mission_id, evidence_id);
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    provider TEXT,
                    capability TEXT,
                    reality_state TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS observations_project_idx ON observations(project_id, observed_at DESC);
                CREATE TABLE IF NOT EXISTS provider_receipts (
                    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    receipt_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS provider_receipts_mission_idx ON provider_receipts(mission_id, receipt_id);
                CREATE TABLE IF NOT EXISTS memory_items (
                    memory_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    source TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    freshness_at TEXT NOT NULL,
                    reality_state TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','superseded','conflicted')),
                    supersedes_memory_id TEXT REFERENCES memory_items(memory_id),
                    conflict_key TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS memory_items_project_idx ON memory_items(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS memory_items_conflict_idx ON memory_items(project_id, conflict_key, status);
                CREATE TABLE IF NOT EXISTS memory_links (
                    link_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    source_memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                    target_memory_id TEXT NOT NULL REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(source_memory_id, target_memory_id, relation)
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL UNIQUE REFERENCES missions(mission_id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    reality_state TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    summary_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS outcomes_project_idx ON outcomes(project_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    mission_id TEXT NOT NULL REFERENCES missions(mission_id) ON DELETE CASCADE,
                    checkpoint_path TEXT NOT NULL,
                    checksum TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS checkpoints_mission_idx ON checkpoints(mission_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS audit_events (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    project_id TEXT REFERENCES projects(project_id),
                    actor_user_id TEXT REFERENCES users(user_id),
                    mission_id TEXT REFERENCES missions(mission_id) ON DELETE SET NULL,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_events_project_idx ON audit_events(project_id, created_at DESC);
                """
            )
            self._ensure_column(db, "projects", "tenant_id", "TEXT")
            self._ensure_column(db, "missions", "tenant_id", "TEXT")
            self._ensure_column(db, "missions", "submission_json", "TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS projects_tenant_idx ON projects(tenant_id, project_id)")
            db.execute("CREATE INDEX IF NOT EXISTS missions_tenant_created_idx ON missions(tenant_id, created_at DESC)")
            now = utc_now()
            db.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", ("legacy", "Legacy imported product state", now))
            db.execute("UPDATE projects SET tenant_id='legacy' WHERE tenant_id IS NULL OR tenant_id=''" )
            db.execute("UPDATE missions SET tenant_id=(SELECT tenant_id FROM projects WHERE projects.project_id=missions.project_id) WHERE tenant_id IS NULL OR tenant_id=''" )

    # ---- Identity and tenancy -------------------------------------------------

    def get_or_create_tenant(self, display_name: str) -> dict[str, Any]:
        tenant_id = _safe_slug(display_name)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO tenants(tenant_id, display_name, created_at) VALUES(?,?,?)", (tenant_id, display_name.strip() or "NEXUS", utc_now()))
            row = db.execute("SELECT * FROM tenants WHERE tenant_id=?", (tenant_id,)).fetchone()
        return dict(row)

    def create_user(self, tenant_id: str, email: str, password: str, role: str = "owner") -> dict[str, Any]:
        normalized = email.strip().lower()
        if "@" not in normalized or len(normalized) > 320:
            raise ValueError("a valid email address is required")
        if role not in {"owner", "operator", "viewer"}:
            raise ValueError("invalid product role")
        now = utc_now()
        with self.connect() as db:
            existing = db.execute("SELECT * FROM users WHERE email=?", (normalized,)).fetchone()
            if existing:
                return dict(existing)
            user_id = f"user-{uuid.uuid4()}"
            db.execute(
                "INSERT INTO users(user_id, tenant_id, email, password_hash, role, active, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (user_id, tenant_id, normalized, _hash_password(password), role, 1, now, now),
            )
            row = db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row)

    def authenticate_password(self, email: str, password: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM users WHERE email=? AND active=1", (email.strip().lower(),)).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            return None
        return dict(row)

    def create_session(self, user_id: str, hours: int) -> tuple[str, dict[str, Any]]:
        raw_token = secrets.token_urlsafe(32)
        now = utc_now()
        session = {
            "session_id": f"session-{uuid.uuid4()}",
            "user_id": user_id,
            "token_hash": hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            "created_at": now,
            "expires_at": utc_after(hours * 3600),
        }
        with self.connect() as db:
            db.execute(
                "INSERT INTO sessions(session_id, user_id, token_hash, created_at, expires_at) VALUES(?,?,?,?,?)",
                (session["session_id"], session["user_id"], session["token_hash"], session["created_at"], session["expires_at"]),
            )
        return raw_token, {key: value for key, value in session.items() if key != "token_hash"}

    def resolve_session(self, raw_token: str) -> dict[str, Any] | None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.connect() as db:
            row = db.execute(
                """SELECT u.user_id, u.tenant_id, u.email, u.role, u.active, s.session_id, s.expires_at
                   FROM sessions s JOIN users u ON u.user_id=s.user_id
                   WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1""",
                (token_hash, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, raw_token: str) -> None:
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        with self.connect() as db:
            db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (utc_now(), token_hash))

    def create_project(self, tenant_id: str, project_id: str, display_name: str) -> dict[str, Any]:
        safe_project = _safe_slug(project_id)
        now = utc_now()
        with self.connect() as db:
            existing = db.execute("SELECT * FROM projects WHERE project_id=?", (safe_project,)).fetchone()
            if existing and existing["tenant_id"] != tenant_id:
                raise PermissionError("project identifier is already owned by another tenant")
            db.execute(
                """INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (safe_project, tenant_id, display_name.strip() or safe_project, now, now),
            )
            row = db.execute("SELECT * FROM projects WHERE project_id=?", (safe_project,)).fetchone()
        return dict(row)

    def grant_project_member(self, project_id: str, user_id: str, role: str) -> None:
        if role not in {"owner", "operator", "viewer"}:
            raise ValueError("invalid project role")
        with self.connect() as db:
            db.execute(
                """INSERT INTO project_memberships(project_id, user_id, role, created_at) VALUES(?,?,?,?)
                   ON CONFLICT(project_id, user_id) DO UPDATE SET role=excluded.role""",
                (project_id, user_id, role, utc_now()),
            )

    def adopt_legacy_projects(self, tenant_id: str) -> list[str]:
        with self.connect() as db:
            rows = db.execute("SELECT project_id FROM projects WHERE tenant_id='legacy'").fetchall()
            project_ids = [row["project_id"] for row in rows]
            if project_ids:
                marks = ",".join("?" for _ in project_ids)
                db.execute(f"UPDATE projects SET tenant_id=?, updated_at=? WHERE project_id IN ({marks})", (tenant_id, utc_now(), *project_ids))
                db.execute(f"UPDATE missions SET tenant_id=? WHERE project_id IN ({marks})", (tenant_id, *project_ids))
        return project_ids

    def list_projects_for_user(self, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.project_id, p.display_name, p.created_at, p.updated_at, pm.role
                   FROM projects p JOIN project_memberships pm ON pm.project_id=p.project_id
                   WHERE pm.user_id=? AND p.tenant_id=? ORDER BY p.updated_at DESC, p.project_id""",
                (user_id, tenant_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def project_role(self, user_id: str, tenant_id: str, project_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT pm.role FROM project_memberships pm JOIN projects p ON p.project_id=pm.project_id
                   WHERE pm.user_id=? AND pm.project_id=? AND p.tenant_id=?""",
                (user_id, _safe_slug(project_id), tenant_id),
            ).fetchone()
        return row["role"] if row else None

    # ---- Mission and queue records ------------------------------------------

    def ensure_project(self, project_id: str, display_name: str | None = None) -> None:
        """Legacy helper retained for local migration only; authenticated code uses create_project."""
        safe_project = _safe_slug(project_id)
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO projects(project_id, tenant_id, display_name, created_at, updated_at) VALUES(?,?,?,?,?)
                   ON CONFLICT(project_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at""",
                (safe_project, "legacy", display_name or safe_project, now, now),
            )

    def create_mission(
        self,
        *,
        mission_id: str,
        tenant_id: str,
        project_id: str,
        scope: str,
        intent: str,
        mode: str,
        capabilities: list[str],
        store_root: str,
        submission: dict[str, Any],
        max_attempts: int,
    ) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO missions(mission_id, tenant_id, project_id, scope, intent, mode, requested_capabilities_json, submission_json, status, reality, verification_status, action_state, store_root, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mission_id, tenant_id, project_id, scope, intent, mode, json.dumps(capabilities), json.dumps(submission), "QUEUED", "PLANNED", "PENDING", "PENDING", store_root, now, now),
            )
            db.execute(
                """INSERT INTO mission_queue(mission_id, status, attempts, max_attempts, available_at, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (mission_id, "QUEUED", 0, max_attempts, now, now, now),
            )
        self.add_event(mission_id, "mission_queued", {"mode": mode, "capabilities": capabilities, "scope": scope})

    def claim_next(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = utc_now()
        claimed: dict[str, Any] | None = None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = db.execute(
                "SELECT mission_id FROM mission_queue WHERE status='LEASED' AND lease_expires_at<?",
                (now,),
            ).fetchall()
            expired_ids = [row["mission_id"] for row in expired]
            if expired_ids:
                placeholders = ",".join("?" for _ in expired_ids)
                db.execute(f"UPDATE mission_queue SET status='QUEUED', lease_owner=NULL, lease_expires_at=NULL, available_at=?, updated_at=? WHERE mission_id IN ({placeholders})", (now, now, *expired_ids))
                db.execute(f"UPDATE missions SET status='QUEUED', updated_at=? WHERE mission_id IN ({placeholders})", (now, *expired_ids))
            candidate = db.execute(
                """SELECT q.mission_id FROM mission_queue q JOIN missions m ON m.mission_id=q.mission_id
                   WHERE q.status='QUEUED' AND m.status='QUEUED' AND q.available_at<=? ORDER BY q.created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if candidate:
                mission_id = candidate["mission_id"]
                updated = db.execute(
                    """UPDATE mission_queue SET status='LEASED', attempts=attempts+1, lease_owner=?, lease_expires_at=?, updated_at=?
                       WHERE mission_id=? AND status='QUEUED'""",
                    (worker_id, utc_after(lease_seconds), now, mission_id),
                )
                if updated.rowcount:
                    db.execute("UPDATE missions SET status='EXECUTING', updated_at=? WHERE mission_id=?", (now, mission_id))
                    claimed = self._mission_row(db.execute(self._mission_select() + " WHERE m.mission_id=?", (mission_id,)).fetchone())
        if claimed:
            self.add_event(claimed["mission_id"], "mission_executing", {"worker_id": worker_id, "attempt": claimed.get("queue", {}).get("attempts")})
        return claimed

    def finish_queue(self, mission_id: str, worker_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """UPDATE mission_queue SET status='COMPLETED', lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE mission_id=? AND lease_owner=?""",
                (utc_now(), mission_id, worker_id),
            )
        self.add_event(mission_id, "queue_settled", {"worker_id": worker_id})

    def retry_or_fail_queue(self, mission_id: str, worker_id: str, error: str) -> bool:
        retry = False
        now = utc_now()
        with self.connect() as db:
            row = db.execute("SELECT attempts, max_attempts FROM mission_queue WHERE mission_id=? AND lease_owner=?", (mission_id, worker_id)).fetchone()
            if row is None:
                return False
            retry = row["attempts"] < row["max_attempts"]
            if retry:
                db.execute(
                    """UPDATE mission_queue SET status='QUEUED', lease_owner=NULL, lease_expires_at=NULL, available_at=?, last_error=?, updated_at=? WHERE mission_id=?""",
                    (utc_after(min(30, 2 ** row["attempts"])), error[:2000], now, mission_id),
                )
                db.execute("UPDATE missions SET status='QUEUED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], now, mission_id))
            else:
                db.execute(
                    """UPDATE mission_queue SET status='FAILED', lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=? WHERE mission_id=?""",
                    (error[:2000], now, mission_id),
                )
                db.execute("UPDATE missions SET status='FAILED', reality='UNKNOWN', verification_status='FAILED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], now, mission_id))
        self.add_event(mission_id, "mission_retry_scheduled" if retry else "mission_failed", {"worker_id": worker_id, "error": error[:2000]})
        return retry

    def mark_running(self, mission_id: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE missions SET status='EXECUTING', updated_at=? WHERE mission_id=?", (utc_now(), mission_id))
        self.add_event(mission_id, "mission_executing", {"compatibility": True})

    def save_result(self, mission_id: str, result: dict[str, Any]) -> None:
        mission = result.get("mission", {})
        verification = mission.get("verification", {}).get("completion_verification", {})
        execution = result.get("execution", {})
        packet = mission.get("action_packet", execution.get("action_packet", {})) or {}
        now = utc_now()
        with self.connect() as db:
            mission_row = db.execute("SELECT tenant_id, project_id FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if mission_row is None:
                raise ValueError("mission does not exist")
            tenant_id, project_id = mission_row["tenant_id"], mission_row["project_id"]
            db.execute(
                """UPDATE missions SET status=?, reality=?, verification_status=?, action_state=?, external_invocations=?, result_json=?, error=NULL, updated_at=? WHERE mission_id=?""",
                (
                    mission.get("state", "UNKNOWN"),
                    mission.get("reality", "UNKNOWN"),
                    verification.get("status", "UNKNOWN"),
                    packet.get("state", "PENDING"),
                    int(execution.get("external_invocations", result.get("external_invocations", 0)) or 0),
                    json.dumps(result, default=str),
                    now,
                    mission_id,
                ),
            )
            db.execute("DELETE FROM mission_evidence WHERE mission_id=?", (mission_id,))
            normalized = execution.get("normalized_observations", []) or mission.get("normalized_observations", [])
            receipts = execution.get("receipts", []) or []
            receipt_by_provider = {receipt.get("provider"): receipt for receipt in receipts if isinstance(receipt, dict)}
            for observation in normalized:
                db.execute(
                    """INSERT INTO mission_evidence(mission_id, capability, provider, observation_id, verification_state, reality, receipt_json, observation_json, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        mission_id,
                        observation.get("capability"),
                        observation.get("provider"),
                        observation.get("observation_id"),
                        observation.get("verification_state"),
                        observation.get("reality"),
                        json.dumps(receipt_by_provider.get(observation.get("provider"), {}), default=str),
                        json.dumps(observation, default=str),
                        now,
                    ),
                )
                observation_id = observation.get("observation_id") or f"observation-{uuid.uuid4()}"
                db.execute(
                    """INSERT OR REPLACE INTO observations(observation_id, tenant_id, project_id, mission_id, provider, capability, reality_state, verification_state, payload_json, observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (observation_id, tenant_id, project_id, mission_id, observation.get("provider"), observation.get("capability"), observation.get("reality", "UNKNOWN"), observation.get("verification_state", "UNKNOWN"), json.dumps(observation, default=str), now),
                )
                db.execute(
                    """INSERT INTO memory_items(memory_id, tenant_id, project_id, mission_id, source, content_json, provenance_json, confidence, freshness_at, reality_state, status, conflict_key, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"memory-{uuid.uuid4()}", tenant_id, project_id, mission_id, observation.get("provider") or "unknown-provider", json.dumps(observation, default=str), json.dumps(receipt_by_provider.get(observation.get("provider"), {}), default=str), "HIGH" if observation.get("verification_state") == "VERIFIED" else "MEDIUM", now, observation.get("reality", "UNKNOWN"), "active", observation.get("capability"), now),
                )
            db.execute("DELETE FROM provider_receipts WHERE mission_id=?", (mission_id,))
            for receipt in receipts:
                if isinstance(receipt, dict):
                    db.execute(
                        "INSERT INTO provider_receipts(tenant_id, project_id, mission_id, provider, receipt_json, created_at) VALUES(?,?,?,?,?,?)",
                        (tenant_id, project_id, mission_id, receipt.get("provider") or "unknown-provider", json.dumps(receipt, default=str), now),
                    )
            db.execute(
                """INSERT INTO outcomes(outcome_id, tenant_id, project_id, mission_id, state, reality_state, verification_state, summary_json, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(mission_id) DO UPDATE SET state=excluded.state, reality_state=excluded.reality_state, verification_state=excluded.verification_state, summary_json=excluded.summary_json, updated_at=excluded.updated_at""",
                (f"outcome-{mission_id}", tenant_id, project_id, mission_id, mission.get("state", "UNKNOWN"), mission.get("reality", "UNKNOWN"), verification.get("status", "UNKNOWN"), json.dumps({"intent": mission.get("intent"), "action_state": packet.get("state", "PENDING"), "external_invocations": execution.get("external_invocations", 0)}, default=str), now, now),
            )
        self.add_event(mission_id, "mission_completed", {"status": mission.get("state"), "verification": verification.get("status"), "external_invocations": execution.get("external_invocations", 0)})

    def save_failure(self, mission_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE missions SET status='FAILED', reality='UNKNOWN', verification_status='FAILED', error=?, updated_at=? WHERE mission_id=?", (error[:2000], utc_now(), mission_id))
        self.add_event(mission_id, "mission_failed", {"error": error[:2000]})

    def add_event(self, mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO mission_events(mission_id, event_type, payload_json, created_at) VALUES(?,?,?,?)", (mission_id, event_type, json.dumps(payload, default=str), utc_now()))

    def add_audit_event(self, tenant_id: str, actor_user_id: str | None, action: str, outcome: str, detail: dict[str, Any], project_id: str | None = None, mission_id: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_events(tenant_id, project_id, actor_user_id, mission_id, action, outcome, detail_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (tenant_id, project_id, actor_user_id, mission_id, action, outcome, json.dumps(detail, default=str), utc_now()),
            )

    def list_memory(self, tenant_id: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM memory_items WHERE tenant_id=? AND project_id=? ORDER BY created_at DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
        return [{**dict(row), "content": json.loads(row["content_json"]), "provenance": json.loads(row["provenance_json"])} for row in rows]

    def list_outcomes(self, tenant_id: str, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM outcomes WHERE tenant_id=? AND project_id=? ORDER BY updated_at DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
        return [{**dict(row), "summary": json.loads(row["summary_json"])} for row in rows]

    def list_audit_events(self, tenant_id: str, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            if project_id:
                rows = db.execute("SELECT * FROM audit_events WHERE tenant_id=? AND project_id=? ORDER BY audit_id DESC LIMIT ?", (tenant_id, _safe_slug(project_id), limit)).fetchall()
            else:
                rows = db.execute("SELECT * FROM audit_events WHERE tenant_id=? ORDER BY audit_id DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]

    def record_checkpoint(self, tenant_id: str, project_id: str, mission_id: str, path: str, checksum: str | None, state: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO checkpoints(checkpoint_id, tenant_id, project_id, mission_id, checkpoint_path, checksum, state, created_at) VALUES(?,?,?,?,?,?,?,?)", (f"checkpoint-{uuid.uuid4()}", tenant_id, _safe_slug(project_id), mission_id, path, checksum, state, utc_now()))

    def list_checkpoints(self, tenant_id: str, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM checkpoints WHERE tenant_id=? AND mission_id=? ORDER BY created_at DESC", (tenant_id, mission_id)).fetchall()
        return [dict(row) for row in rows]

    def control_mission(self, mission_id: str, state: str) -> bool:
        if state not in {"PAUSED", "QUEUED", "CANCELLED"}:
            raise ValueError("unsupported mission control state")
        now = utc_now()
        with self.connect() as db:
            mission = db.execute("SELECT status FROM missions WHERE mission_id=?", (mission_id,)).fetchone()
            if mission is None or mission["status"] == "EXECUTING":
                return False
            if state == "CANCELLED":
                db.execute("UPDATE mission_queue SET status='FAILED', lease_owner=NULL, lease_expires_at=NULL, last_error='cancelled by authenticated operator', updated_at=? WHERE mission_id=?", (now, mission_id))
            elif state == "QUEUED":
                db.execute("UPDATE mission_queue SET status='QUEUED', available_at=?, lease_owner=NULL, lease_expires_at=NULL, updated_at=? WHERE mission_id=?", (now, now, mission_id))
            db.execute("UPDATE missions SET status=?, updated_at=? WHERE mission_id=?", (state, now, mission_id))
        self.add_event(mission_id, f"mission_{state.lower()}", {"control_state": state})
        return True

    @staticmethod
    def _mission_select() -> str:
        return """SELECT m.*, q.status AS queue_status, q.attempts AS queue_attempts, q.max_attempts AS queue_max_attempts,
                         q.available_at AS queue_available_at, q.lease_owner AS queue_lease_owner, q.lease_expires_at AS queue_lease_expires_at, q.last_error AS queue_last_error
                  FROM missions m LEFT JOIN mission_queue q ON q.mission_id=m.mission_id"""

    def _mission_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["requested_capabilities"] = json.loads(result.pop("requested_capabilities_json"))
        raw_result = result.pop("result_json")
        result["result"] = json.loads(raw_result) if raw_result else None
        raw_submission = result.pop("submission_json", None)
        result["submission"] = json.loads(raw_submission) if raw_submission else None
        result["queue"] = {
            "status": result.pop("queue_status", None),
            "attempts": result.pop("queue_attempts", None),
            "max_attempts": result.pop("queue_max_attempts", None),
            "available_at": result.pop("queue_available_at", None),
            "lease_expires_at": result.pop("queue_lease_expires_at", None),
            "last_error": result.pop("queue_last_error", None),
        }
        result.pop("queue_lease_owner", None)
        return result

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(self._mission_select() + " WHERE m.mission_id=?", (mission_id,)).fetchone()
        return self._mission_row(row)

    def list_missions(self, project_id: str, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(self._mission_select() + " WHERE m.project_id=? ORDER BY m.created_at DESC LIMIT ?", (_safe_slug(project_id), limit)).fetchall()
        return [self._mission_row(row) for row in rows]

    def evidence(self, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM mission_evidence WHERE mission_id=? ORDER BY evidence_id", (mission_id,)).fetchall()
        records = []
        for row in rows:
            item = dict(row)
            item["receipt"] = json.loads(item.pop("receipt_json") or "{}")
            item["observation"] = json.loads(item.pop("observation_json") or "{}")
            records.append(item)
        return records

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM mission_events WHERE mission_id=? ORDER BY event_id", (mission_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def queue_health(self) -> dict[str, int]:
        with self.connect() as db:
            rows = db.execute("SELECT status, COUNT(*) AS count FROM mission_queue GROUP BY status").fetchall()
        counts = {row["status"].lower(): row["count"] for row in rows}
        return {"queued": counts.get("queued", 0), "leased": counts.get("leased", 0), "completed": counts.get("completed", 0), "failed": counts.get("failed", 0)}

    def health(self) -> dict[str, Any]:
        self.migrate()
        with self.connect() as db:
            missions = db.execute("SELECT COUNT(*) AS count FROM missions").fetchone()["count"]
            projects = db.execute("SELECT COUNT(*) AS count FROM projects").fetchone()["count"]
            users = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        return {"database": "sqlite", "path": str(self.path), "missions": missions, "projects": projects, "users": users, "queue": self.queue_health(), "status": "HEALTHY"}

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup through the database engine, including WAL state."""
        target = Path(destination).expanduser().resolve()
        if target == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as source:
            backup = sqlite3.connect(target)
            try:
                source.backup(backup)
            finally:
                backup.close()
        return target

    def restore_from(self, source_path: str | Path) -> Path:
        """Replace the live database from a verified backup; callers must stop API and workers first."""
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError("backup source does not exist")
        if source == self.path.resolve():
            raise ValueError("restore source must differ from the live database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.path)
        self.migrate()
        return self.path
