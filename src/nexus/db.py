from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'researcher' CHECK (role IN ('administrator', 'researcher')),
            created_at TEXT NOT NULL,
            disabled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            revoked_at TEXT
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(owner_id, name)
        );

        CREATE TABLE IF NOT EXISTS project_memberships (
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('owner', 'contributor', 'viewer')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (project_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
            project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
            request_id TEXT NOT NULL,
            action TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
        CREATE INDEX IF NOT EXISTS idx_memberships_user_id ON project_memberships(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor_user_id);
        """,
    ),
)


class Database:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            }
            for version, migration_sql in MIGRATIONS:
                if version in applied:
                    continue
                connection.executescript(migration_sql)
                connection.execute(
                    """
                    INSERT INTO schema_migrations(version, applied_at)
                    VALUES (?, datetime('now'))
                    """,
                    (version,),
                )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def check(self) -> bool:
        with self.connection() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1
