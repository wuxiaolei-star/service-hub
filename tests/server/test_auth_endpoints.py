"""HTTP contract tests for login, sessions, and admin bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _settings(tmp_path: Path, mode: str = "required") -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode=mode),  # type: ignore[arg-type]
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


def _read_bootstrap(client: TestClient) -> dict[str, str]:
    path = client.app.state.settings.storage.root / "bootstrap-admin.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def test_login_rejects_unknown_user_and_wrong_password_with_same_error(client) -> None:
    unknown = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "whatever"})
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"

    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "another"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_bootstrap_admin_can_login_and_gets_change_flag(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        bootstrap_file = tmp_path / "data" / "bootstrap-admin.json"
        assert bootstrap_file.is_file()
        if os.name == "posix":  # Windows ignores POSIX permission bits.
            assert bootstrap_file.stat().st_mode & 0o777 == 0o600

        credentials = json.loads(bootstrap_file.read_text(encoding="utf-8"))
        assert credentials["username"] == "admin"
        assert len(credentials["password"]) >= 20

        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": credentials["username"],
                "password": credentials["password"],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["role"] == "admin"
        assert payload["must_change_password"] is True
        assert "hub_session" in response.cookies


def test_me_and_logout_lifecycle(client: TestClient) -> None:
    credentials = _read_bootstrap(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["must_change_password"] is True

    logout = client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 204

    after = client.get("/api/v1/auth/me", headers=headers)
    assert after.status_code == 401


def test_change_password_updates_hash_and_clears_flag(client: TestClient) -> None:
    credentials = _read_bootstrap(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    changed = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": credentials["password"], "new_password": "brand-new-passw0rd"},
        headers=headers,
    )
    assert changed.status_code == 200

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["must_change_password"] is False

    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": credentials["username"], "password": "brand-new-passw0rd"},
    )
    assert relogin.status_code == 200


def test_change_password_rejects_weak_new_password(client: TestClient) -> None:
    credentials = _read_bootstrap(client)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": credentials["username"], "password": credentials["password"]},
    )
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    response = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": credentials["password"], "new_password": "short"},
        headers=headers,
    )
    assert response.status_code == 422


def test_bootstrap_writes_file_only_when_users_are_empty(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))):
        bootstrap_file = tmp_path / "data" / "bootstrap-admin.json"
        first = bootstrap_file.read_text(encoding="utf-8")

    with TestClient(create_app(_settings(tmp_path))):
        assert bootstrap_file.read_text(encoding="utf-8") == first


def test_off_mode_does_not_create_bootstrap_file(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path, mode="off"))):
        assert not (tmp_path / "data" / "bootstrap-admin.json").is_file()
