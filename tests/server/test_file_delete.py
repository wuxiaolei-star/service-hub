"""File self-service deletion contract tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import FileRecord, Job, JobFile, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from sqlalchemy.orm import Session


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
    """Serve the file API without authentication for deletion behaviour tests."""
    with TestClient(create_app(_settings(tmp_path, "off"))) as test_client:
        yield test_client


def _upload(client: TestClient, name: str = "doomed.nc") -> str:
    created = client.post("/api/v1/files", files={"file": (name, b"payload-bytes")})
    assert created.status_code == 201
    return str(created.json()["file_id"])


def _seed_job_referencing(session: Session, file_record: FileRecord) -> None:
    """Create one minimal Build/Job/JobFile chain that pins the given file."""
    plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
    version = PluginVersion(
        plugin=plugin,
        version="1.0.0",
        spec_version="1.0",
        sdk_version="1.0",
        source_sha256="a" * 64,
        manifest_json={},
        status="INSTALLED",
    )
    build = PluginBuild(
        build_key="plugin_build_delete_test",
        manifest_build_id="build-delete-test",
        plugin_version=version,
        target_os="linux",
        target_arch="amd64",
        runtime_type="docker",
        python_version="3.12",
        sdk_version="1.0",
        package_path="plugins/plugin_build_delete_test",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/env.tar.zst",
        runtime_fingerprint="c" * 64,
        build_metadata_json={},
        status="ENABLED",
    )
    session.add_all([plugin, version, build])
    session.flush()
    job = Job(
        plugin_build=build,
        runtime_type="docker",
        runtime_fingerprint="c" * 64,
        status="PENDING",
        params_json={},
        inputs_json={"source_nc": file_record.file_key},
        timeout_seconds=30,
    )
    session.add(job)
    session.flush()
    session.add(
        JobFile(
            job=job,
            file_record=file_record,
            role="INPUT",
            logical_name="source_nc",
            file_snapshot_json={"file_id": file_record.file_key},
        )
    )


def test_delete_unreferenced_file_returns_204_removes_payload_and_audits(
    client: TestClient, tmp_path: Path
) -> None:
    """An unreferenced upload must vanish from disk, metadata, and be audited."""
    file_id = _upload(client)
    payload = tmp_path / "data" / "uploads" / file_id / "payload"
    assert payload.exists()

    response = client.delete(f"/api/v1/files/{file_id}")

    assert response.status_code == 204
    assert not payload.exists()
    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    logs = client.get("/api/v1/audit-logs", params={"action": "file.delete"}).json()["items"]
    assert len(logs) == 1
    assert logs[0]["resource_type"] == "file"
    assert logs[0]["resource_id"] == file_id
    assert logs[0]["result"] == "ok"


def test_delete_file_referenced_by_job_returns_409_and_keeps_file(client: TestClient) -> None:
    """A Job-referenced file must stay intact and report the conflict code."""
    file_id = _upload(client)
    with client.app.state.session_factory() as session:
        file_record = session.query(FileRecord).filter_by(file_key=file_id).one()
        _seed_job_referencing(session, file_record)
        session.commit()

    response = client.delete(f"/api/v1/files/{file_id}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FILE_IN_USE"
    assert client.get(f"/api/v1/files/{file_id}").status_code == 200


def test_delete_missing_file_returns_404(client: TestClient) -> None:
    """Deleting an unknown file key must use the stable file-not-found error."""
    response = client.delete("/api/v1/files/file_missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_delete_non_available_file_returns_404(client: TestClient) -> None:
    """A record whose status is not AVAILABLE must behave like a missing file."""
    with client.app.state.session_factory() as session:
        session.add(
            FileRecord(
                file_key="file_deadbeefdeadbeefdeadbeefdeadbeef",
                logical_name="gone.nc",
                original_filename="gone.nc",
                relative_path="uploads/file_deadbeefdeadbeefdeadbeefdeadbeef/payload",
                size_bytes=1,
                sha256="1" * 64,
                status="DELETED",
            )
        )
        session.commit()

    response = client.delete("/api/v1/files/file_deadbeefdeadbeefdeadbeefdeadbeef")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_delete_requires_operator_role(tmp_path: Path) -> None:
    """Roles below operator must be rejected before any deletion happens."""
    with TestClient(create_app(_settings(tmp_path, "required"))) as required_client:
        bootstrap = tmp_path / "data" / "bootstrap-admin.json"
        payload = json.loads(bootstrap.read_text(encoding="utf-8"))
        admin_login = required_client.post(
            "/api/v1/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['token']}"}
        required_client.post(
            "/api/v1/users",
            json={"username": "plain_viewer", "password": "long-enough-pass", "role": "viewer"},
            headers=admin_headers,
        )
        viewer_login = required_client.post(
            "/api/v1/auth/login", json={"username": "plain_viewer", "password": "long-enough-pass"}
        )
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['token']}"}

        denied = required_client.delete("/api/v1/files/file_missing", headers=viewer_headers)
        required_client.cookies.clear()
        anonymous = required_client.delete("/api/v1/files/file_missing")

        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"
        assert anonymous.status_code == 401
