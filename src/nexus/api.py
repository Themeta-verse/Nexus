from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from .config import Settings
from .db import Database
from .rate_limit import SlidingWindowRateLimiter
from .repositories import (
    create_project,
    create_session,
    create_user,
    get_authorized_project,
    get_session_with_user,
    get_user_by_email,
    list_projects,
    revoke_session,
    touch_session,
    write_audit_event,
)
from .schemas import (
    AuthResponse,
    HealthResponse,
    LoginRequest,
    ProjectCreateRequest,
    ProjectResponse,
    RegisterRequest,
    UserResponse,
)
from .security import (
    Principal,
    SecurityValidationError,
    hash_password,
    hash_session_token,
    issue_session_token,
    normalize_email,
    parse_timestamp,
    session_expiry,
    utc_now,
    validate_password,
    verify_password,
)

router = APIRouter()
DUMMY_PASSWORD_HASH = hash_password("NexusDummyPasswordA1")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def request_id(request: Request) -> str:
    return request.state.request_id


def get_rate_limiter(request: Request) -> SlidingWindowRateLimiter:
    return request.app.state.rate_limiter


def _user_response(row: dict) -> UserResponse:
    return UserResponse(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        created_at=row["created_at"],
    )


def _authorization_error(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise _authorization_error()
    token = authorization[7:].strip()
    if not token or len(token) > 512:
        raise _authorization_error()

    database = get_database(request)
    settings = get_settings(request)
    token_hash = hash_session_token(token, settings.session_pepper)
    now = utc_now()
    with database.connection() as connection:
        session = get_session_with_user(connection, token_hash)
        if session is None:
            raise _authorization_error()
        if session["revoked_at"] or session["disabled_at"]:
            raise _authorization_error()
        try:
            expired = parse_timestamp(session["expires_at"]) <= now
        except ValueError:
            expired = True
        if expired:
            raise _authorization_error("Session expired")
        touch_session(connection, session["session_id"], now)
        return Principal(
            user_id=session["user_id"],
            email=session["email"],
            role=session["role"],
            session_id=session["session_id"],
        )


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
def liveness(request: Request) -> HealthResponse:
    settings = get_settings(request)
    return HealthResponse(status="ok", service=settings.app_name)


@router.get("/health/ready", response_model=HealthResponse, tags=["health"])
def readiness(request: Request) -> HealthResponse:
    settings = get_settings(request)
    if not get_database(request).check():
        raise HTTPException(status_code=503, detail="Service is not ready")
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@router.post("/api/v1/auth/register", response_model=AuthResponse, status_code=201, tags=["auth"])
def register(
    payload: RegisterRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    rid: str = Depends(request_id),
    rate_limiter: SlidingWindowRateLimiter = Depends(get_rate_limiter),
) -> AuthResponse:
    client_host = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(f"register:{client_host}"):
        raise HTTPException(status_code=429, detail="Too many authentication attempts")
    try:
        email = normalize_email(payload.email)
        validate_password(payload.password)
        password_hash = hash_password(payload.password)
    except SecurityValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = utc_now()
    token = issue_session_token()
    expires_at = session_expiry(settings.session_ttl_seconds)
    with database.connection() as connection:
        if get_user_by_email(connection, email) is not None:
            write_audit_event(
                connection,
                actor_user_id=None,
                project_id=None,
                request_id=rid,
                action="auth.register",
                outcome="denied",
                metadata={"reason": "duplicate_email"},
                now=now,
            )
            raise HTTPException(status_code=409, detail="Unable to create account")
        try:
            user = create_user(connection, email, password_hash, now)
            create_session(
                connection,
                user["id"],
                hash_session_token(token, settings.session_pepper),
                now,
                expires_at,
            )
            write_audit_event(
                connection,
                actor_user_id=user["id"],
                project_id=None,
                request_id=rid,
                action="auth.register",
                outcome="success",
                metadata={},
                now=now,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Unable to create account") from exc

    return AuthResponse(access_token=token, expires_at=expires_at, user=_user_response(user))


@router.post("/api/v1/auth/login", response_model=AuthResponse, tags=["auth"])
def login(
    payload: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    database: Database = Depends(get_database),
    rid: str = Depends(request_id),
    rate_limiter: SlidingWindowRateLimiter = Depends(get_rate_limiter),
) -> AuthResponse:
    client_host = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(f"login:{client_host}"):
        raise HTTPException(status_code=429, detail="Too many authentication attempts")
    try:
        email = normalize_email(payload.email)
    except SecurityValidationError as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc

    now = utc_now()
    token = issue_session_token()
    with database.connection() as connection:
        row = get_user_by_email(connection, email)
        password_ok = verify_password(
            payload.password, row["password_hash"] if row else DUMMY_PASSWORD_HASH
        )
        if row is None or not password_ok or row["disabled_at"]:
            write_audit_event(
                connection,
                actor_user_id=row["id"] if row else None,
                project_id=None,
                request_id=rid,
                action="auth.login",
                outcome="denied",
                metadata={"reason": "invalid_credentials"},
                now=now,
            )
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        expires_at = session_expiry(settings.session_ttl_seconds)
        create_session(
            connection,
            row["id"],
            hash_session_token(token, settings.session_pepper),
            now,
            expires_at,
        )
        write_audit_event(
            connection,
            actor_user_id=row["id"],
            project_id=None,
            request_id=rid,
            action="auth.login",
            outcome="success",
            metadata={},
            now=now,
        )
        user = {
            "id": row["id"],
            "email": row["email"],
            "role": row["role"],
            "created_at": row["created_at"],
        }
    return AuthResponse(access_token=token, expires_at=expires_at, user=_user_response(user))


@router.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout(
    request: Request,
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
    rid: str = Depends(request_id),
) -> None:
    now = utc_now()
    with database.connection() as connection:
        revoke_session(connection, principal.session_id, now)
        write_audit_event(
            connection,
            actor_user_id=principal.user_id,
            project_id=None,
            request_id=rid,
            action="auth.logout",
            outcome="success",
            metadata={},
            now=now,
        )


@router.get("/api/v1/me", response_model=UserResponse, tags=["auth"])
def me(
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
) -> UserResponse:
    with database.connection() as connection:
        row = connection.execute(
            "SELECT id, email, role, created_at FROM users WHERE id = ?",
            (principal.user_id,),
        ).fetchone()
    if row is None:
        raise _authorization_error()
    return _user_response(dict(row))


@router.post("/api/v1/projects", response_model=ProjectResponse, status_code=201, tags=["projects"])
def create_project_endpoint(
    payload: ProjectCreateRequest,
    request: Request,
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
    rid: str = Depends(request_id),
) -> ProjectResponse:
    if not payload.name:
        raise HTTPException(status_code=422, detail="Project name cannot be empty")
    now = utc_now()
    with database.connection() as connection:
        try:
            project = create_project(
                connection, principal.user_id, payload.name, payload.description, now
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="A project with that name already exists"
            ) from exc
        write_audit_event(
            connection,
            actor_user_id=principal.user_id,
            project_id=project["id"],
            request_id=rid,
            action="project.create",
            outcome="success",
            metadata={},
            now=now,
        )
    project["membership_role"] = "owner"
    return ProjectResponse(**project)


@router.get("/api/v1/projects", response_model=list[ProjectResponse], tags=["projects"])
def projects(
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
) -> list[ProjectResponse]:
    with database.connection() as connection:
        rows = list_projects(connection, principal.user_id)
    return [ProjectResponse(**row) for row in rows]


@router.get("/api/v1/projects/{project_id}", response_model=ProjectResponse, tags=["projects"])
def project(
    project_id: str,
    principal: Principal = Depends(require_principal),
    database: Database = Depends(get_database),
) -> ProjectResponse:
    if len(project_id) != 32 or any(
        character not in "0123456789abcdef" for character in project_id
    ):
        raise HTTPException(status_code=404, detail="Project not found")
    with database.connection() as connection:
        row = get_authorized_project(connection, project_id, principal.user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**row)
