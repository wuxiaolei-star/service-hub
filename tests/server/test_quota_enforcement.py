"""Quota enforcement contract tests for uploads and job creation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.services.auth import hash_password
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

from tests.server.test_jobs_api import _seed_build


def _settings(
    tmp_path: Path,
    *,
    quotas: QuotasSettings | None = None,
) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=quotas or QuotasSettings(),
    )


def _add_user(client: TestClient, username: str, role: str, **quota: int) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(
            UserRecord(
                username=username,
                password_hash=hash_password("password-secret"),
                role=role,
                **quota,
            )
        )
        session.commit()


def _token_for(client: TestClient, username: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        from hub_server.services.auth import AuthService

        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _client(tmp_path: Path, quotas: QuotasSettings | None = None) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, quotas=quotas)))


def _upload(client: TestClient, token: str, payload: bytes, name: str = "a.nc") -> object:
    return client.post(
        "/api/v1/files",
        files={"file": (name, payload, "application/x-netcdf")},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_file_count_quota_uses_user_override_then_409(tmp_path: Path) -> None:
    quotas = QuotasSettings(max_file_count=100)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator", quota_file_count=1)
        token = _token_for(client, "op")

        assert _upload(client, token, b"data-1").status_code == 201  # type: ignore[union-attr]
        second = _upload(client, token, b"data-2")
        assert second.status_code == 409  # type: ignore[union-attr]
        body = second.json()["error"]  # type: ignore[union-attr]
        assert body["code"] == "QUOTA_EXCEEDED"
        assert body["details"]["quota"] == "file_count"


def test_total_bytes_quota_uses_user_override(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _add_user(client, "op", "operator", quota_total_bytes=10)
        token = _token_for(client, "op")

        response = _upload(client, token, b"x" * 20)
        assert response.status_code == 409  # type: ignore[union-attr]
        body = response.json()["error"]  # type: ignore[union-attr]
        assert body["details"]["quota"] == "total_bytes"
        assert body["details"]["limit"] == 10


def test_admin_role_is_exempt_from_user_quotas(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _add_user(client, "boss", "admin", quota_file_count=1)
        token = _token_for(client, "boss")

        assert _upload(client, token, b"one").status_code == 201  # type: ignore[union-attr]
        assert _upload(client, token, b"two", name="b.nc").status_code == 201  # type: ignore[union-attr]


def test_disabled_quotas_allow_everything(tmp_path: Path) -> None:
    quotas = QuotasSettings(enabled=False, max_file_count=1)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator", quota_file_count=1)
        token = _token_for(client, "op")

        assert _upload(client, token, b"one").status_code == 201  # type: ignore[union-attr]
        assert _upload(client, token, b"two", name="b.nc").status_code == 201  # type: ignore[union-attr]


def test_api_key_actor_is_limited_only_by_global_bytes(tmp_path: Path) -> None:
    quotas = QuotasSettings(max_total_bytes=10)
    with _client(tmp_path, quotas) as client:
        _add_user(client, "op", "operator")
        operator_token = _token_for(client, "op")

        from hub_server.services.auth import AuthService

        factory = client.app.state.session_factory
        with factory() as session:
            service = AuthService(session)
            _record, key = service.create_api_key(name="ci", role="operator")
            session.commit()

        response = _upload(client, key, b"x" * 20)
        assert response.status_code == 409  # type: ignore[union-attr]
        assert response.json()["error"]["details"]["quota"] == "global_bytes"  # type: ignore[union-attr]

        headers = {"Authorization": f"Bearer {operator_token}"}
        list_response = client.get("/api/v1/files", headers=headers)
        assert list_response.status_code == 200
        assert list_response.json()["items"] == []


def test_job_concurrency_quota_per_user(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_build(client, runtime_type="docker")
        _add_user(client, "op", "operator", quota_concurrent_jobs=1)
        token = _token_for(client, "op")
        headers = {"Authorization": f"Bearer {token}"}

        uploaded = _upload(client, token, b"nc")
        assert uploaded.status_code == 201  # type: ignore[union-attr]
        file_id = uploaded.json()["file_id"]  # type: ignore[union-attr]

        first = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "docker",
                "inputs": {"source_nc": file_id},
                "params": {},
            },
        )
        assert first.status_code == 201

        second = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "docker",
                "inputs": {"source_nc": file_id},
                "params": {},
            },
        )
        assert second.status_code == 409  # type: ignore[union-attr]
        body = second.json()["error"]  # type: ignore[union-attr]
        assert body["code"] == "QUOTA_EXCEEDED"
        assert body["details"]["quota"] == "concurrent_jobs"
