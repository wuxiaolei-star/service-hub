"""HTTP contract tests for user and API key management."""

from __future__ import annotations

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
def client(tmp_path: Path) -> TestClient:
    """Required-mode app bootstrapped with the seeded admin account."""
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        bootstrap = test_client.app.state.settings.storage.root / "bootstrap-admin.json"
        credentials = pytest.importorskip("json").loads(bootstrap.read_text(encoding="utf-8"))
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": credentials["username"], "password": credentials["password"]},
        )
        test_client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
        yield test_client


def test_admin_can_create_and_list_users(client: TestClient) -> None:
    created = client.post(
        "/api/v1/users",
        json={"username": "operator_wang", "password": "long-enough-pass", "role": "operator"},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["username"] == "operator_wang"
    assert payload["role"] == "operator"
    assert "password" not in payload
    assert "password_hash" not in payload

    listed = client.get("/api/v1/users")
    usernames = [item["username"] for item in listed.json()["items"]]
    assert usernames == ["admin", "operator_wang"]


def test_create_user_rejects_weak_password_and_bad_role(client: TestClient) -> None:
    weak = client.post(
        "/api/v1/users", json={"username": "weak", "password": "short", "role": "viewer"}
    )
    assert weak.status_code == 422

    bad_role = client.post(
        "/api/v1/users",
        json={"username": "bad", "password": "long-enough-pass", "role": "superuser"},
    )
    assert bad_role.status_code == 422


def test_admin_can_disable_user_and_reset_password(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"username": "temp_user", "password": "long-enough-pass", "role": "viewer"},
    )
    listed = client.get("/api/v1/users").json()["items"]
    user_id = next(item["id"] for item in listed if item["username"] == "temp_user")

    reset = client.post(f"/api/v1/users/{user_id}/reset-password")
    assert reset.status_code == 200
    new_password = str(reset.json()["password"])
    assert len(new_password) >= 20

    relogin = client.post(
        "/api/v1/auth/login",
        json={"username": "temp_user", "password": new_password},
    )
    assert relogin.status_code == 200
    assert relogin.json()["must_change_password"] is True

    disabled = client.post(f"/api/v1/users/{user_id}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "temp_user", "password": new_password},
    )
    assert blocked.status_code == 401


def test_api_key_lifecycle_via_api(client: TestClient) -> None:
    created = client.post(
        "/api/v1/api-keys", json={"name": "业务系统A", "role": "operator"}
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["key"].startswith("hub_")
    assert payload["key_prefix"] == payload["key"][:12]
    assert "key_hash" not in payload

    listed = client.get("/api/v1/api-keys").json()["items"]
    entry = next(item for item in listed if item["name"] == "业务系统A")
    assert entry["key_prefix"] == payload["key_prefix"]
    assert "key_hash" not in entry

    revoked = client.post(f"/api/v1/api-keys/{entry['id']}/revoke")
    assert revoked.status_code == 200

    rejected = client.get(
        "/api/v1/files", headers={"Authorization": f"Bearer {payload['key']}"}
    )
    assert rejected.status_code == 401
