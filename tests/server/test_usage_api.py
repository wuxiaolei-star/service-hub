"""Usage endpoint contract tests for per-user quota visibility."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.services.auth import AuthService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
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
        quotas=QuotasSettings(),
    )


def _admin_headers(client: TestClient) -> dict[str, str]:

    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def test_usage_reports_owner_storage_and_count(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = _admin_headers(client)

        created = client.post(
            "/api/v1/users",
            json={"username": "op", "password": "long-enough-pass", "role": "operator"},
        )
        user_id = created.json()["id"]
        op_login = client.post(
            "/api/v1/auth/login", json={"username": "op", "password": "long-enough-pass"}
        )
        op_headers = {"Authorization": f"Bearer {op_login.json()['token']}"}

        upload = client.post(
            "/api/v1/files",
            files={"file": ("a.nc", b"nc-bytes")},
            headers=op_headers,
        )
        assert upload.status_code == 201

        usage = client.get(f"/api/v1/users/{user_id}/usage", headers=headers)
        assert usage.status_code == 200
        payload = usage.json()
        assert payload["total_bytes"] == len(b"nc-bytes")
        assert payload["file_count"] == 1
        assert payload["active_jobs"] == 0


def test_usage_requires_admin(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = _admin_headers(client)

        created = client.post(
            "/api/v1/users",
            json={"username": "op2", "password": "long-enough-pass", "role": "operator"},
        )
        user_id = created.json()["id"]
        op_login = client.post(
            "/api/v1/auth/login", json={"username": "op2", "password": "long-enough-pass"}
        )
        op_headers = {"Authorization": f"Bearer {op_login.json()['token']}"}

        denied = client.get(f"/api/v1/users/{user_id}/usage", headers=op_headers)
        assert denied.status_code == 403
        _ = headers
