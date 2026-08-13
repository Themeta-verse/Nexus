from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from .security import isoformat, new_id


def create_user(
    connection: sqlite3.Connection, email: str, password_hash: str, now: datetime
) -> dict[str, Any]:
    user_id = new_id()
    connection.execute(
        """
        INSERT INTO users(id, email, password_hash, role, created_at)
        VALUES (?, ?, ?, 'researcher', ?)
        """,
        (user_id, email, password_hash, isoformat(now)),
    )
    return {
        "id": user_id,
        "email": email,
        "role": "researcher",
        "created_at": isoformat(now),
    }


def get_user_by_email(connection: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def get_user_by_id(connection: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_session(
    connection: sqlite3.Connection,
    user_id: str,
    token_hash: str,
    created_at: datetime,
    expires_at: datetime,
) -> str:
    session_id = new_id()
    connection.execute(
        """
        INSERT INTO sessions(id, user_id, token_hash, created_at, expires_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            session_id,
            user_id,
            token_hash,
            isoformat(created_at),
            isoformat(expires_at),
            isoformat(created_at),
        ),
    )
    return session_id


def get_session_with_user(connection: sqlite3.Connection, token_hash: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT s.id AS session_id, s.user_id, s.expires_at, s.revoked_at,
               u.email, u.role, u.disabled_at
        FROM sessions AS s
        JOIN users AS u ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()


def touch_session(connection: sqlite3.Connection, session_id: str, now: datetime) -> None:
    connection.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE id = ?", (isoformat(now), session_id)
    )


def revoke_session(connection: sqlite3.Connection, session_id: str, now: datetime) -> None:
    connection.execute(
        "UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?",
        (isoformat(now), session_id),
    )


def create_project(
    connection: sqlite3.Connection,
    owner_id: str,
    name: str,
    description: str,
    now: datetime,
) -> dict[str, Any]:
    project_id = new_id()
    timestamp = isoformat(now)
    connection.execute(
        """
        INSERT INTO projects(id, owner_id, name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (project_id, owner_id, name, description, timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO project_memberships(project_id, user_id, role, created_at)
        VALUES (?, ?, 'owner', ?)
        """,
        (project_id, owner_id, timestamp),
    )
    return {
        "id": project_id,
        "owner_id": owner_id,
        "name": name,
        "description": description,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def list_projects(connection: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT p.id, p.owner_id, p.name, p.description, p.created_at, p.updated_at,
               m.role AS membership_role
        FROM projects AS p
        JOIN project_memberships AS m ON m.project_id = p.id
        WHERE m.user_id = ?
        ORDER BY p.created_at DESC, p.id DESC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_authorized_project(
    connection: sqlite3.Connection, project_id: str, user_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT p.id, p.owner_id, p.name, p.description, p.created_at, p.updated_at,
               m.role AS membership_role
        FROM projects AS p
        JOIN project_memberships AS m ON m.project_id = p.id
        WHERE p.id = ? AND m.user_id = ?
        """,
        (project_id, user_id),
    ).fetchone()
    return dict(row) if row else None


def write_audit_event(
    connection: sqlite3.Connection,
    *,
    actor_user_id: str | None,
    project_id: str | None,
    request_id: str,
    action: str,
    outcome: str,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO audit_events(
            id, actor_user_id, project_id, request_id, action, outcome, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            new_id(),
            actor_user_id,
            project_id,
            request_id,
            action,
            outcome,
            json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
            isoformat(now),
        ),
    )
