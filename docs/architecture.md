# Nexus Architecture — Sprint 1

## Purpose

Nexus is being bootstrapped as a production-grade machine-learning experimentation and learning platform. Sprint 1 establishes a small but real backend foundation. It does not claim to implement dataset ingestion, model training, experiment tracking, deployment, monitoring, or agent capabilities yet.

## Technology boundary

The first implementation uses Python 3.11+ with FastAPI for the HTTP boundary and SQLite for a local, transactional development database. The persistence layer is isolated behind a repository module so that a PostgreSQL deployment adapter can be introduced in a later sprint without changing API contracts. No secret is committed, and runtime configuration is supplied through environment variables or the deployment platform’s secret manager.

## Components

| Component | Responsibility | Sprint 1 status |
|---|---|---|
| `nexus.config` | Parse and validate runtime configuration | Implemented |
| `nexus.db` | Open the database, enable integrity settings, and apply versioned migrations | Implemented |
| `nexus.security` | Password hashing, opaque session tokens, constant-time comparisons, and authorization helpers | Implemented |
| `nexus.repositories` | Transactional user, project, membership, and audit persistence | Implemented |
| `nexus.api` | Health, registration, login, logout, current-user, and project endpoints | Implemented |
| `nexus.main` | Application factory, middleware, exception handling, and route assembly | Implemented |
| ML engine | Genuine training, prediction, and evaluation | Not implemented; explicitly deferred |
| Dataset platform | Ingestion, validation, versioning, and lineage | Not implemented; explicitly deferred |
| Model registry/deployment | Governance, promotion, serving, and rollback | Not implemented; explicitly deferred |
| Agents | Tool authorization and governed execution | Not implemented; explicitly deferred |

## Security boundaries

The browser is untrusted. Every protected endpoint authenticates the bearer session on the server and checks resource ownership or membership before returning project data. Passwords are never stored directly; only a salted, memory-hard password hash is stored. Session tokens are opaque and only their SHA-256 digests are stored in the database, with expiry and revocation fields.

Audit events are written in the same database transaction as security-sensitive mutations. The API does not log authorization headers, passwords, session tokens, or request bodies. Request bodies are bounded by middleware, and a simple process-local request limiter protects authentication routes in this single-process foundation. A distributed limiter is required before horizontal production scaling.

The local SQLite database is suitable for development and tests, not a claim of production database readiness. Production deployment must use encrypted storage, managed secrets, TLS, backups, restoration testing, and a reviewed PostgreSQL migration path.

## Data model

Sprint 1 creates the following tables with foreign keys, uniqueness constraints, indexes, and immutable audit records:

- `users`: identity, email, password hash, role, and lifecycle timestamps.
- `sessions`: hashed opaque bearer tokens, expiry, revocation, and last-use timestamps.
- `projects`: project ownership and lifecycle timestamps.
- `project_memberships`: scoped user-to-project authorization with unique membership constraints.
- `audit_events`: append-only security and lifecycle events with actor, project, action, outcome, and request ID.

## API scope

The initial API is intentionally small:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Liveness without sensitive internals |
| `GET` | `/health/ready` | Readiness including database connectivity |
| `POST` | `/api/v1/auth/register` | Create the first or subsequent user account |
| `POST` | `/api/v1/auth/login` | Authenticate and issue an expiring opaque session |
| `POST` | `/api/v1/auth/logout` | Revoke the current session |
| `GET` | `/api/v1/me` | Return the authenticated user’s non-sensitive identity |
| `POST` | `/api/v1/projects` | Create a project owned by the authenticated user |
| `GET` | `/api/v1/projects` | List only projects visible to the authenticated user |
| `GET` | `/api/v1/projects/{project_id}` | Read one authorized project |

No endpoint accepts arbitrary SQL, executes uploaded code, loads model artifacts, or exposes credentials.

## Explicit limitations

This sprint is a foundation, not a complete ML platform. It does not provide MFA, password recovery, email verification, federated identity, distributed sessions, a production secret manager integration, TLS termination, a PostgreSQL deployment, background workers, object storage, model training, dataset processing, or compliance certification. These limitations are documented rather than hidden behind UI claims.
