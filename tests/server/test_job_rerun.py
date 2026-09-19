"""Job rerun endpoint tests: provenance, terminal precondition, and audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Job, PluginBuild, UserRecord
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

from tests.server.test_jobs_api import _seed_build


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
        assert login.status_code == 200, login.text
        test_client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
        yield test_client


def _upload_nc(client: TestClient) -> str:
    upload = client.post(
        "/api/v1/files",
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert upload.status_code == 201, upload.text
    return str(upload.json()["file_id"])


def _create_job(
    client: TestClient,
    *,
    params: dict[str, object] | None = None,
    file_id: str | None = None,
) -> dict[str, object]:
    """Seed one enabled Build and create a PENDING Job through the public API."""
    _seed_build(client, runtime_type="docker", include_metrics=True)
    if file_id is None:
        file_id = _upload_nc(client)
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": params or {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _set_status(client: TestClient, job_key: str, status_value: str) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        job = session.query(Job).filter_by(job_key=job_key).one()
        job.status = status_value
        session.commit()


def _viewer_token(client: TestClient) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        session.add(
            UserRecord(
                username="watcher",
                password_hash=hash_password("watcher-password"),
                role="viewer",
            )
        )
        session.commit()
        user = session.query(UserRecord).filter_by(username="watcher").one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return token


def test_rerun_terminal_job_creates_new_pending_job(client: TestClient) -> None:
    original = _create_job(client)
    _set_status(client, str(original["job_id"]), "FAILED")

    response = client.post(f"/api/v1/jobs/{original['job_id']}/rerun")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"] != original["job_id"]
    assert body["replayed_from"] == original["job_id"]
    assert body["status"] == "PENDING"
    assert body["plugin_id"] == original["plugin_id"]
    assert body["version"] == original["version"]
    assert body["runtime_type"] == original["runtime_type"]
    assert body["build_id"] == original["build_id"]
    assert body["cancel_requested"] is False


def test_rerun_replays_params_and_inputs_snapshot(client: TestClient) -> None:
    file_id = _upload_nc(client)
    original = _create_job(client, params={"metrics": ["depth"]}, file_id=file_id)
    _set_status(client, str(original["job_id"]), "FAILED")

    response = client.post(f"/api/v1/jobs/{original['job_id']}/rerun")

    assert response.status_code == 201, response.text
    factory = client.app.state.session_factory
    with factory() as session:
        source = session.query(Job).filter_by(job_key=str(original["job_id"])).one()
        replay = session.query(Job).filter_by(job_key=response.json()["job_id"]).one()
        assert replay.params_json == source.params_json == {"metrics": ["depth"]}
        assert replay.inputs_json == source.inputs_json == {"source_nc": file_id}
        assert replay.replayed_from == source.job_key


def test_rerun_records_owner_and_audit_entry(client: TestClient) -> None:
    original = _create_job(client)
    _set_status(client, str(original["job_id"]), "SUCCESS")

    response = client.post(f"/api/v1/jobs/{original['job_id']}/rerun")

    assert response.status_code == 201, response.text
    factory = client.app.state.session_factory
    with factory() as session:
        admin = session.query(UserRecord).filter_by(username="admin").one()
        replay = session.query(Job).filter_by(job_key=response.json()["job_id"]).one()
        assert replay.owner_user_id == admin.id
        entry = session.query(AuditLogRecord).filter_by(action="job.rerun").one()
        assert entry.actor_type == "user"
        assert entry.actor_id == admin.id
        assert entry.actor_name == "admin"
        assert entry.resource_type == "job"
        assert entry.resource_id == response.json()["job_id"]
        assert entry.detail == {"replayed_from": str(original["job_id"])}
        assert entry.result == "ok"


def test_rerun_of_rerun_points_at_immediate_original(client: TestClient) -> None:
    first = _create_job(client)
    _set_status(client, str(first["job_id"]), "FAILED")
    second = client.post(f"/api/v1/jobs/{first['job_id']}/rerun").json()
    _set_status(client, str(second["job_id"]), "FAILED")

    response = client.post(f"/api/v1/jobs/{second['job_id']}/rerun")

    assert response.status_code == 201, response.text
    assert response.json()["replayed_from"] == second["job_id"]


def test_rerun_non_terminal_job_returns_409(client: TestClient) -> None:
    original = _create_job(client)  # freshly created Jobs are PENDING

    response = client.post(f"/api/v1/jobs/{original['job_id']}/rerun")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JOB_NOT_TERMINAL"
    factory = client.app.state.session_factory
    with factory() as session:
        assert session.query(Job).count() == 1


def test_rerun_missing_job_returns_404(client: TestClient) -> None:
    response = client.post("/api/v1/jobs/job_missing/rerun")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_rerun_revalidates_build_still_enabled(client: TestClient) -> None:
    """A rerun is a fresh creation, so a disabled Build must be refused again."""
    original = _create_job(client)
    _set_status(client, str(original["job_id"]), "FAILED")
    factory = client.app.state.session_factory
    with factory() as session:
        build = session.query(PluginBuild).filter_by(build_key="plugin_build_docker").one()
        build.status = "DISABLED"
        session.commit()

    response = client.post(f"/api/v1/jobs/{original['job_id']}/rerun")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_ENABLED"


def test_rerun_requires_operator_role(client: TestClient) -> None:
    token = _viewer_token(client)
    original = _create_job(client)
    _set_status(client, str(original["job_id"]), "FAILED")

    response = client.post(
        f"/api/v1/jobs/{original['job_id']}/rerun",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
