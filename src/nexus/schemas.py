from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(RegisterRequest):
    pass


class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)

    @field_validator("name", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.strip()


class ProjectResponse(BaseModel):
    id: str
    owner_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    membership_role: str


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str | None = None


class ErrorResponse(BaseModel):
    detail: str
