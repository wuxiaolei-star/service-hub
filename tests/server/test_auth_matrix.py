"""Role matrix contract tests for the authenticated public API."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.services.auth import AuthService, hash_password
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _settings(tmp_path: Path, mode: str) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode=mode),  # type: ignore[arg-type]
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Serve the API in required mode with one user per role seeded."""
    test_client = TestClient(create_app(_settings(tmp_path, "required")))
    with test_client:
        factory = test_client.app.state.session_factory
        with factory() as session:
            for role in ("viewer", "operator", "publisher", "admin"):
                session.add(
                    UserRecord(
                        username=role,
                        password_hash=hash_password("password-secret"),
                        role=role,
                    )
                )
            session.commit()
        yield test_client


def _token_for(client: TestClient, role: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == role).one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


CASES = [
    ("GET", "/api/v1/files", "viewer", 200),
    ("GET", "/api/v1/files", None, 401),
    ("GET", "/api/v1/system/info", "viewer", 200),
    ("GET", "/api/v1/system/info", None, 401),
    ("GET", "/api/v1/plugins", "viewer", 200),
    ("GET", "/api/v1/plugin-builds", "viewer", 200),
    ("GET", "/api/v1/jobs", "viewer", 200),
    ("POST", "/api/v1/files", "viewer", 403),
    ("POST", "/api/v1/files", "operator", 422),  # 通过鉴权后缺 multipart 才 422
    ("GET", "/api/v1/files/none/download", "viewer", 403),
    ("GET", "/api/v1/files/none/download", "operator", 404),
    ("POST", "/api/v1/jobs", "viewer", 403),
    ("POST", "/api/v1/jobs", "operator", 422),  # 通过鉴权后缺 body 才 422
    ("POST", "/api/v1/jobs/none/cancel", "viewer", 403),
    ("POST", "/api/v1/jobs/none/cancel", "operator", 404),
    ("POST", "/api/v1/plugins/install", "operator", 403),
    ("POST", "/api/v1/plugins/install", "publisher", 422),  # 通过鉴权后缺 multipart 才 422
    ("POST", "/api/v1/plugin-builds/none/enable", "publisher", 404),
    ("POST", "/api/v1/plugin-builds/none/enable", "operator", 403),
    ("POST", "/api/v1/plugin-builds/none/disable", "publisher", 404),
]


@pytest.mark.parametrize(("method", "path", "role", "expected"), CASES)
def test_role_matrix(
    client: TestClient, method: str, path: str, role: str | None, expected: int
) -> None:
    """Every public endpoint must enforce the documented role matrix."""
    headers = _bearer(_token_for(client, role)) if role is not None else {}
    response = client.request(method, path, headers=headers)
    assert response.status_code == expected, (
        f"{method} {path} as {role}: expected {expected}, got "
        f"{response.status_code}: {response.text}"
    )


def test_health_stays_public_without_credentials(client: TestClient) -> None:
    assert client.get("/api/v1/system/health").json() == {"status": "UP"}


def test_off_mode_grants_full_access(tmp_path: Path) -> None:
    """HUB_AUTH_MODE=off must behave exactly like V1 without credentials."""
    test_client = TestClient(create_app(_settings(tmp_path, "off")))
    with test_client:
        assert test_client.get("/api/v1/system/health").json() == {"status": "UP"}
        assert test_client.get("/api/v1/system/info").status_code == 200
        assert test_client.get("/api/v1/files").status_code == 200
        assert test_client.get("/api/v1/jobs").status_code == 200


def test_api_key_can_authenticate(client: TestClient) -> None:
    """An API key acts as an operator for both read and write endpoints."""
    factory = client.app.state.session_factory
    with factory() as session:
        service = AuthService(session)
        _record, plaintext = service.create_api_key(name="ci", role="operator")
        session.commit()

    headers = {"Authorization": f"Bearer {plaintext}"}
    assert client.get("/api/v1/files", headers=headers).status_code == 200
    assert client.get("/api/v1/system/info", headers=headers).status_code == 200
    assert client.post("/api/v1/jobs", headers=headers).status_code == 422


def test_invalid_bearer_is_rejected(client: TestClient) -> None:
    response = client.get(
        "/api/v1/files", headers={"Authorization": "Bearer hub_invalidinvalid"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
