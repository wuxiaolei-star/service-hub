"""Webhook callback listing endpoint contract tests (viewer-gated)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, PluginBuild, UserRecord
from hub_server.routers.webhooks import router as webhooks_router
from hub_server.services.auth import AuthService, hash_password
from hub_server.services.webhooks import enqueue_callback
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


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(webhooks_router, prefix="/api/v1")
    return TestClient(app)


def _admin_headers(client: TestClient) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _token_for(client: TestClient, username: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _seed_job(client: TestClient, *, status: str = "SUCCESS") -> tuple[str, int]:
    """Seed one enabled Build plus a terminal Job; return (job_key, job_id)."""
    _seed_build(client, runtime_type="docker")
    factory = client.app.state.session_factory
    with factory() as session:
        build = session.query(PluginBuild).filter_by(build_key="plugin_build_docker").one()
        job = Job(
            plugin_build=build,
            runtime_type="docker",
            runtime_fingerprint="x",
            status=status,
            params_json={},
            inputs_json={},
            timeout_seconds=60,
            cancel_requested=False,
        )
        session.add(job)
        session.commit()
        return job.job_key, job.id


def test_callbacks_require_authentication(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/jobs/job_unknown/callbacks")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_callbacks_reject_unknown_job(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)

        response = client.get("/api/v1/jobs/job_unknown/callbacks", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_callbacks_listed_without_secret(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        factory = client.app.state.session_factory
        with factory() as session:
            enqueue_callback(
                session, job_id, "https://hooks.example.test/done", secret="top-secret"
            )
            session.commit()

        response = client.get(f"/api/v1/jobs/{job_key}/callbacks", headers=headers)

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["url"] == "https://hooks.example.test/done"
        assert item["state"] == "PENDING"
        assert item["attempts"] == 0
        assert item["last_status_code"] is None
        assert item["last_error"] is None
        assert item["next_attempt_at"] is None
        assert "secret" not in item


def test_callbacks_allow_viewer_role(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        job_key, _job_id = _seed_job(client)
        factory = client.app.state.session_factory
        with factory() as session:
            session.add(
                UserRecord(
                    username="watcher",
                    password_hash=hash_password("password-secret"),
                    role="viewer",
                )
            )
            session.commit()
        headers = {"Authorization": f"Bearer {_token_for(client, 'watcher')}"}

        response = client.get(f"/api/v1/jobs/{job_key}/callbacks", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
