"""Privilege-clamping contract tests for API key creation."""

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


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Required-mode app seeded with one operator and one admin user."""
    test_client = TestClient(create_app(_settings(tmp_path)))
    with test_client:
        factory = test_client.app.state.session_factory
        with factory() as session:
            for role in ("operator", "admin"):
                session.add(
                    UserRecord(
                        username=f"u_{role}",
                        password_hash=hash_password("password-secret"),
                        role=role,
                    )
                )
            session.commit()
        yield test_client


def _bearer_token(client: TestClient, role: str) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == f"u_{role}").one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_operator_cannot_create_admin_key(client: TestClient) -> None:
    """An operator must not mint a key whose role outranks their own."""
    response = client.post(
        "/api/v1/api-keys",
        json={"name": "escalated", "role": "admin"},
        headers=_bearer_token(client, "operator"),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_operator_can_create_operator_key(client: TestClient) -> None:
    """Creating a key at one's own role stays allowed."""
    response = client.post(
        "/api/v1/api-keys",
        json={"name": "peer", "role": "operator"},
        headers=_bearer_token(client, "operator"),
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["role"] == "operator"
    assert payload["key"].startswith("hub_")


def test_admin_can_create_admin_key(client: TestClient) -> None:
    """Admin already holds the top role, so admin keys stay available."""
    response = client.post(
        "/api/v1/api-keys",
        json={"name": "root-automation", "role": "admin"},
        headers=_bearer_token(client, "admin"),
    )

    assert response.status_code == 201
    assert response.json()["role"] == "admin"


def test_operator_api_key_cannot_create_admin_key(client: TestClient) -> None:
    """An API key acts at its own role, so an operator key cannot escalate either."""
    factory = client.app.state.session_factory
    with factory() as session:
        _record, plaintext = AuthService(session).create_api_key(name="ci", role="operator")
        session.commit()

    response = client.post(
        "/api/v1/api-keys",
        json={"name": "escalated", "role": "admin"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
