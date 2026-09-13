"""Metrics endpoint contract tests (admin only, Prometheus text format)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.services.auth import AuthService, hash_password
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


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_settings(tmp_path)))


def test_metrics_requires_admin(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        anonymous = client.get("/api/v1/system/metrics")
        assert anonymous.status_code == 401


def test_metrics_outputs_prometheus_text(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        response = client.get("/api/v1/system/metrics", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        for expected in (
            "# HELP hub_jobs_total",
            "# TYPE hub_jobs_total gauge",
            "hub_jobs_active 0",
            "hub_files_total 0",
            "hub_files_bytes_total 0",
            "hub_users_total 1",
            "hub_uptime_seconds ",
        ):
            assert expected in body, f"missing {expected!r} in:\n{body}"


def test_metrics_reflect_created_users_and_files(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        client.post(
            "/api/v1/users",
            headers=headers,
            json={"username": "op", "password": "long-enough-pass", "role": "operator"},
        )
        body = client.get("/api/v1/system/metrics", headers=headers).text
        assert "hub_users_total 2" in body
