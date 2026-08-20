from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware

from .schemas import LoginRequest, MemoryLifecycleRequest, MissionSubmission, OwnerSetupRequest, ProjectCreateRequest
from .service import StandaloneMissionService


def create_app(service: StandaloneMissionService | None = None) -> FastAPI:
    runtime = service or StandaloneMissionService()
    app = FastAPI(title="NEXUS Independent API", version="0.2.0", description="Authenticated evidence-first NEXUS mission runtime.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime.settings.web_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    def bearer_token(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer authentication is required")
        token = authorization[7:].strip()
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer authentication is required")
        return token

    def principal(token: str = Depends(bearer_token)) -> dict:
        identity = runtime.authenticate_bearer(token)
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session is invalid, expired, or revoked")
        return identity

    def public_mission(mission: dict) -> dict:
        hidden = {"tenant_id", "store_root", "submission"}
        return {key: value for key, value in mission.items() if key not in hidden}

    @app.get("/health")
    def health() -> dict:
        return runtime.public_health()

    @app.get("/api/v1/health")
    def authenticated_health(identity: dict = Depends(principal)) -> dict:
        del identity
        return runtime.health()

    @app.post("/api/v1/auth/login")
    def login(credentials: LoginRequest) -> dict:
        session = runtime.login(credentials.email, credentials.password)
        if not session:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
        return session

    @app.get("/api/v1/setup/status")
    def setup_status() -> dict:
        return runtime.setup_status()

    @app.post("/api/v1/setup/owner", status_code=status.HTTP_201_CREATED)
    def setup_owner(request: OwnerSetupRequest) -> dict:
        try:
            return runtime.setup_initial_owner(request.email, request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="initial owner setup is unavailable for this runtime") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
    def register_owner(request: OwnerSetupRequest) -> dict:
        try:
            return runtime.register_owner_workspace(request.email, request.password)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="owner workspace registration is disabled for this runtime") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(token: str = Depends(bearer_token)) -> Response:
        runtime.logout(token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/me")
    def current_user(identity: dict = Depends(principal)) -> dict:
        return {"user": identity, "projects": runtime.list_projects(identity)}

    @app.get("/api/v1/projects")
    def list_projects(identity: dict = Depends(principal)) -> dict:
        return {"projects": runtime.list_projects(identity)}

    @app.post("/api/v1/projects", status_code=status.HTTP_201_CREATED)
    def create_project(request: ProjectCreateRequest, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.create_project(identity, request.project_id, request.display_name)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/missions")
    def list_missions(project_id: str, limit: int = Query(default=30, ge=1, le=100), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "missions": [public_mission(mission) for mission in runtime.list_missions(identity, project_id, limit)]}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/memory")
    def project_memory(project_id: str, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "memory": runtime.list_memory(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/memory/{memory_id}")
    def update_memory(project_id: str, memory_id: str, request: MemoryLifecycleRequest, identity: dict = Depends(principal)) -> dict:
        try:
            memory = runtime.update_memory(identity, project_id, memory_id, request.action, request.note)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if memory is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory record not found")
        return {"memory": memory}

    @app.get("/api/v1/projects/{project_id}/context")
    def project_context(project_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.project_context(identity, project_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}/outcomes")
    def project_outcomes(project_id: str, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"project_id": project_id, "outcomes": runtime.list_outcomes(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/audit-events")
    def audit_events(project_id: str | None = None, limit: int = Query(default=100, ge=1, le=200), identity: dict = Depends(principal)) -> dict:
        try:
            return {"audit_events": runtime.list_audit_events(identity, project_id, limit)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/capabilities")
    def capabilities(identity: dict = Depends(principal)) -> dict:
        return {"capabilities": runtime.capabilities(identity)}

    @app.get("/api/v1/providers")
    def providers(identity: dict = Depends(principal)) -> dict:
        return {"providers": runtime.providers(identity)}

    @app.get("/api/v1/operator/database")
    def database_inspection(identity: dict = Depends(principal)) -> dict:
        try:
            return runtime.database_inspection(identity)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/diagnostics")
    def diagnostics(identity: dict = Depends(principal)) -> dict:
        return runtime.diagnostics(identity)

    @app.post("/api/v1/missions", status_code=status.HTTP_202_ACCEPTED)
    def create_mission(submission: MissionSubmission, identity: dict = Depends(principal)) -> dict:
        try:
            return public_mission(runtime.enqueue_mission(identity, submission))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}")
    def get_mission(mission_id: str, include_result: bool = False, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id, include_result=include_result)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if mission is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        return public_mission(mission)

    @app.get("/api/v1/missions/{mission_id}/evidence")
    def get_evidence(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "evidence": runtime.mission_evidence(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}/events")
    def get_events(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "events": runtime.mission_events(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/api/v1/missions/{mission_id}/checkpoints")
    def checkpoints(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.get_mission(identity, mission_id)
            if mission is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
            return {"mission_id": mission_id, "checkpoints": runtime.mission_checkpoints(identity, mission_id)}
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.post("/api/v1/missions/{mission_id}/control/{control}")
    def control_mission(mission_id: str, control: str, identity: dict = Depends(principal)) -> dict:
        try:
            mission = runtime.control_mission(identity, mission_id, control)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if mission is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        return public_mission(mission)

    @app.post("/api/v1/missions/{mission_id}/recover")
    def recover(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.recover(identity, mission_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        result["mission"] = public_mission(result["mission"])
        return result

    @app.post("/api/v1/missions/{mission_id}/continue")
    def continue_mission(mission_id: str, identity: dict = Depends(principal)) -> dict:
        try:
            result = runtime.continue_mission(identity, mission_id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="mission not found")
        result["mission"] = public_mission(result["mission"])
        return result

    return app


app = create_app()
