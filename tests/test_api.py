from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus.config import Settings
from nexus.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        app_name="Nexus Test",
        environment="test",
        database_path=tmp_path / "nexus-test.sqlite3",
        session_ttl_seconds=3600,
        session_pepper="test-only-pepper-that-is-long-enough-123456",
        max_body_bytes=100_000,
        login_rate_limit=10,
        login_rate_window_seconds=60,
        generated_ephemeral_pepper=False,
    )
    return TestClient(create_app(settings))


def register(client: TestClient, email: str = "alice@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "CorrectHorse9!"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth_headers(auth: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth['access_token']}"}


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ok"
    assert ready.headers["X-Content-Type-Options"] == "nosniff"


def test_register_login_me_and_logout(client: TestClient) -> None:
    auth = register(client)
    assert auth["user"]["email"] == "alice@example.com"

    me = client.get("/api/v1/me", headers=auth_headers(auth))
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"
    assert "password" not in me.text

    logout = client.post("/api/v1/auth/logout", headers=auth_headers(auth))
    assert logout.status_code == 204
    assert client.get("/api/v1/me", headers=auth_headers(auth)).status_code == 401

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "ALICE@example.com", "password": "CorrectHorse9!"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == auth["user"]["id"]


def test_duplicate_and_invalid_credentials_are_safe(client: TestClient) -> None:
    register(client)
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "CorrectHorse9!"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Unable to create account"

    invalid = client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": "WrongPassword9!"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["detail"] == "Invalid credentials"
    assert "unknown@example.com" not in invalid.text


def test_password_policy_and_unknown_fields(client: TestClient) -> None:
    weak = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "weak"},
    )
    assert weak.status_code == 422

    extra = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "CorrectHorse9!", "role": "administrator"},
    )
    assert extra.status_code == 422


def test_project_resource_isolation(client: TestClient) -> None:
    alice = register(client, "alice@example.com")
    bob = register(client, "bob@example.com")

    created = client.post(
        "/api/v1/projects",
        headers=auth_headers(alice),
        json={"name": "private project", "description": "not for bob"},
    )
    assert created.status_code == 201
    project = created.json()

    alice_list = client.get("/api/v1/projects", headers=auth_headers(alice))
    assert [item["id"] for item in alice_list.json()] == [project["id"]]

    bob_list = client.get("/api/v1/projects", headers=auth_headers(bob))
    assert bob_list.status_code == 200
    assert bob_list.json() == []

    forbidden_read = client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers(bob))
    assert forbidden_read.status_code == 404


def test_request_body_limit(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        headers={"Content-Length": "100001"},
        content=b"x",
    )
    assert response.status_code == 413
