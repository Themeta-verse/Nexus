# Nexus ML Laboratory

Nexus is the foundation of a **production-grade machine-learning experimentation and learning platform**. The long-term system will cover dataset governance, reproducible experiments, genuine model training and evaluation, model management, deployment, monitoring, research workflows, and eventually permission-controlled ML agents.

## Current status

Sprint 1 establishes a real backend foundation. It is intentionally modest and does not pretend that future ML capabilities already exist.

| Capability | Status |
|---|---|
| FastAPI application factory | Implemented |
| Versioned SQLite migration | Implemented for local development and tests |
| Password hashing with salted scrypt | Implemented |
| Opaque expiring bearer sessions | Implemented |
| Server-side authentication and authorization | Implemented |
| Project ownership and isolation | Implemented |
| Append-only security audit events | Implemented |
| Request IDs, safe errors, body-size guard, security headers | Implemented |
| Dataset ingestion and validation | Not implemented |
| Genuine model training and evaluation | Not implemented |
| Experiment tracking | Not implemented |
| Model registry and deployment | Not implemented |
| Monitoring, drift detection, and retraining | Not implemented |
| Agent execution | Not implemented |

> **Integrity rule:** metrics, predictions, training results, deployment status, and security claims will only be presented after the underlying capability exists and is tested.

## Local development

The service requires Python 3.11 or newer. The project does not use a `.env` file. Runtime configuration is supplied through environment variables or a deployment platform’s secret manager.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
pytest -q
ruff check .
uvicorn nexus.main:app --app-dir src --reload
```

The default local database is `data/nexus.sqlite3`, which is ignored by Git. A different location can be supplied with `NEXUS_DATABASE_PATH`. Production requires a persistent, encrypted database and a reviewed PostgreSQL migration path; the local SQLite default is not a production-readiness claim.

## Runtime configuration

| Variable | Default | Purpose |
|---|---|---|
| `NEXUS_ENVIRONMENT` | `development` | `development`, `test`, `staging`, or `production` |
| `NEXUS_DATABASE_PATH` | `data/nexus.sqlite3` | Local database path |
| `NEXUS_SESSION_PEPPER` | Ephemeral in non-production | Secret pepper for session-token digests; at least 32 characters is mandatory in production |
| `NEXUS_SESSION_TTL_SECONDS` | `3600` | Session lifetime |
| `NEXUS_MAX_BODY_BYTES` | `1000000` | Maximum request body size checked from `Content-Length` |
| `NEXUS_LOGIN_RATE_LIMIT` | `10` | Authentication attempts per process and window |
| `NEXUS_LOGIN_RATE_WINDOW_SECONDS` | `60` | Authentication rate-limit window |

The ephemeral development pepper invalidates sessions after a process restart. This is deliberate. A stable production pepper must be injected through a managed secret facility and must never be committed to Git, placed in frontend code, or written to `.env`.

## API surface

| Method | Path | Description |
|---|---|---|
| `GET` | `/health/live` | Liveness check |
| `GET` | `/health/ready` | Database-backed readiness check |
| `POST` | `/api/v1/auth/register` | Register and issue a session |
| `POST` | `/api/v1/auth/login` | Authenticate and issue a session |
| `POST` | `/api/v1/auth/logout` | Revoke the current session |
| `GET` | `/api/v1/me` | Read the authenticated user’s non-sensitive identity |
| `POST` | `/api/v1/projects` | Create an owned project |
| `GET` | `/api/v1/projects` | List projects visible to the current user |
| `GET` | `/api/v1/projects/{project_id}` | Read an authorized project |

Interactive API documentation is available at `/docs` outside production mode.

## Security posture and limitations

Passwords are stored only as salted scrypt-derived hashes. Sessions use opaque random bearer tokens; only HMAC-SHA-256 digests derived with the server-side pepper are persisted. Protected project reads use server-side membership queries, which prevents a user from reading another user’s project by changing an identifier. Authentication and project mutations create audit events in the same database transaction as the mutation.

This sprint does not provide MFA, account recovery, email verification, federated identity, distributed rate limiting, TLS termination, managed secret storage, production backups, object storage, background workers, or compliance certification. The process-local limiter must be replaced with shared infrastructure before horizontal scaling. These are known limitations, not hidden features.

See [`docs/architecture.md`](docs/architecture.md) for the design and [`SECURITY.md`](SECURITY.md) for repository-level security rules.
