"""Audit trail contract tests for authenticated write operations."""

from __future__ import annotations

import json
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
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        bootstrap = test_client.app.state.settings.storage.root / "bootstrap-admin.json"
        payload = json.loads(bootstrap.read_text(encoding="utf-8"))
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        test_client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
        yield test_client


def test_login_failures_and_success_are_audited(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as bare_client:
        failed = bare_client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
        assert failed.status_code == 401

        bootstrap = tmp_path / "data" / "bootstrap-admin.json"
        payload = json.loads(bootstrap.read_text(encoding="utf-8"))
        login = bare_client.post(
            "/api/v1/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        assert login.status_code == 200
        token = login.json()["token"]

        logs = bare_client.get(
            "/api/v1/audit-logs", headers={"Authorization": f"Bearer {token}"}
        ).json()["items"]
        actions = [entry["action"] for entry in logs]
        assert "auth.login" in actions
        assert "auth.login_failed" in actions

        by_action = {entry["action"]: entry for entry in logs}
        failed_entry = by_action["auth.login_failed"]
        assert failed_entry["result"] == "denied"
        ok_entry = by_action["auth.login"]
        assert ok_entry["result"] == "ok"
        assert ok_entry["actor_type"] == "user"


def test_file_upload_is_audited_with_resource(client: TestClient) -> None:
    uploaded = client.post(
        "/api/v1/files", files={"file": ("audit.nc", b"nc-bytes")}
    )
    assert uploaded.status_code == 201
    file_id = str(uploaded.json()["file_id"])

    logs = client.get("/api/v1/audit-logs", params={"action": "file.upload"}).json()["items"]
    assert len(logs) == 1
    assert logs[0]["resource_type"] == "file"
    assert logs[0]["resource_id"] == file_id
    assert logs[0]["actor_name"] == "admin"


def test_audit_logs_require_admin(client: TestClient) -> None:
    created = client.post(
        "/api/v1/users",
        json={"username": "plain_operator", "password": "long-enough-pass", "role": "operator"},
    )
    user_id = created.json()["id"]
    login = client.post(
        "/api/v1/auth/login", json={"username": "plain_operator", "password": "long-enough-pass"}
    )
    operator_token = login.json()["token"]

    denied = client.get(
        "/api/v1/audit-logs", headers={"Authorization": f"Bearer {operator_token}"}
    )
    assert denied.status_code == 403

    del user_id


def test_audit_logs_support_actor_and_action_filter(client: TestClient) -> None:
    client.post(
        "/api/v1/users",
        json={"username": "audited_user", "password": "long-enough-pass", "role": "viewer"},
    )
    filtered = client.get("/api/v1/audit-logs", params={"action": "user.create"}).json()
    assert all(entry["action"] == "user.create" for entry in filtered["items"])
    assert filtered["items"]


def test_user_governance_audit_rows_carry_the_real_actor(client: TestClient) -> None:
    """Governance rows name the identity that acted, not a hardcoded "admin" (L-9).

    All five users/api-keys mutations used to write ``actor_id=None`` with a
    literal "admin" name, so an action performed by an operator (or via an API
    key) was unattributable in the audit trail.
    """
    from hub_server.models import AuditLogRecord, UserRecord

    factory = client.app.state.session_factory
    with factory() as session:
        admin_id = session.query(UserRecord).filter_by(username="admin").one().id

    created = client.post(
        "/api/v1/users",
        json={"username": "governed_op", "password": "long-enough-pass", "role": "operator"},
    )
    assert created.status_code == 201
    user_id = int(created.json()["id"])

    operator_login = client.post(
        "/api/v1/auth/login", json={"username": "governed_op", "password": "long-enough-pass"}
    )
    operator_headers = {"Authorization": f"Bearer {operator_login.json()['token']}"}

    key = client.post(
        "/api/v1/api-keys",
        json={"name": "governed-key", "role": "operator"},
        headers=operator_headers,
    )
    assert key.status_code == 201
    key_id = int(key.json()["id"])
    revoked = client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=operator_headers)
    assert revoked.status_code == 200

    assert client.post(f"/api/v1/users/{user_id}/disable").status_code == 200
    reset = client.post(f"/api/v1/users/{user_id}/reset-password")
    assert reset.status_code == 200

    with factory() as session:
        rows = {
            row.action: row
            for row in session.query(AuditLogRecord)
            .filter(
                AuditLogRecord.action.in_(
                    [
                        "user.create",
                        "user.disable",
                        "user.reset_password",
                        "api_key.create",
                        "api_key.revoke",
                    ]
                )
            )
            .all()
        }
    assert set(rows) == {
        "user.create",
        "user.disable",
        "user.reset_password",
        "api_key.create",
        "api_key.revoke",
    }
    admin_rows = ("user.create", "user.disable", "user.reset_password")
    for action in admin_rows:
        row = rows[action]
        assert (row.actor_type, row.actor_id, row.actor_name) == ("user", admin_id, "admin")
        assert row.ip is not None, f"{action} must attribute a source address"
    for action in ("api_key.create", "api_key.revoke"):
        row = rows[action]
        assert (row.actor_type, row.actor_id, row.actor_name) == (
            "user",
            user_id,
            "governed_op",
        )
        assert row.ip is not None, f"{action} must attribute a source address"
