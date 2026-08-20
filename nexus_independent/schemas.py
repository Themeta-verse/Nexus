from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field, field_validator


READ_CAPABILITIES = {"repository.read", "repository.metadata.read", "browser.read", "filesystem.read"}


class MissionSubmission(BaseModel):
    intent: str = Field(min_length=3, max_length=4000)
    project_id: str = Field(default="local", min_length=1, max_length=100)
    scope: str = Field(default="Themeta-verse/Nexus", min_length=1, max_length=300)
    mode: Literal["REAL_READ", "SIMULATION"] = "SIMULATION"
    capabilities: list[str] | None = None
    repository_scope: str | None = None
    browser_url: str | None = None
    filesystem_path: str | None = None

    @field_validator("capabilities")
    @classmethod
    def supported_capabilities(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        unsupported = sorted(set(unique) - READ_CAPABILITIES)
        if unsupported:
            raise ValueError(f"unsupported or consequential capability request: {', '.join(unsupported)}")
        return unique


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class ProjectCreateRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=160)


class MemoryLifecycleRequest(BaseModel):
    action: Literal["retire", "restore", "annotate"]
    note: str | None = Field(default=None, max_length=1000)


class MissionResponse(BaseModel):
    mission_id: str
    project_id: str
    status: str
    reality: str
    verification_status: str
    action_state: str
    external_invocations: int
    queue: dict | None = None
    result: dict | None = None
