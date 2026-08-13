# Sprint 1 — Production Foundation

## Objective

Create the smallest coherent, secure backend foundation for the Nexus ML Engineering Laboratory while preserving the long-term requirement for genuine ML, reproducible experimentation, resource isolation, and governed lifecycle management.

## Implementation scope

The sprint adds a FastAPI application factory, runtime configuration, a versioned SQLite migration, password hashing, opaque sessions, registration, login, logout, current-user access, project creation, project listing, project reads, server-side project membership checks, request IDs, body-size checks, security headers, and transactional audit events.

## Security requirements

Passwords must not be stored directly. Bearer tokens must not be stored directly. Protected resources must be authorized server-side. Duplicate-account and invalid-login responses must not disclose sensitive account details. Authentication traffic must be rate-limited within the single-process scope. Exceptions must not expose stack traces or secrets to clients. No `.env` file or credential may be introduced.

## Testing requirements

The test suite covers liveness and readiness, registration, login, logout and revocation, current-user access, invalid credentials, password policy, unknown request fields, project ownership, cross-user isolation, request-size rejection, salted password hashes, and session-token pepper separation.

## Integration requirements

The service starts through `nexus.main:app` or `uvicorn nexus.main:app --app-dir src`. Database initialization applies migration version 1 before serving requests. The storage layer is isolated so a reviewed production database adapter can be added later without redefining the API contract.

## Documentation requirements

README, architecture, security policy, and this sprint record describe what exists and what remains deferred. No documentation claims compliance certification, end-to-end encryption for server-processed data, genuine ML results, or production deployment readiness.

## Git requirements

Before commit and push, run tests, lint, secret-pattern checks, `.env` checks, and a diff review. Local databases and generated files must remain untracked. The commit must be focused on Sprint 1.

## Definition of done

The sprint is complete only when the implementation passes its tests and lint checks, resource isolation is tested, security boundaries are documented, no secrets or `.env` files are present, the diff is reviewed, and all deferred capabilities are explicitly labeled as not implemented.
