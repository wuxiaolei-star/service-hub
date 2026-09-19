"""CSV export contract tests for audit logs and jobs."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Job, Plugin, PluginBuild, PluginVersion
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
    """Serve the API without authentication for export behaviour tests."""
    with TestClient(create_app(_settings(tmp_path, "off"))) as test_client:
        yield test_client


def _seed_build(session: Session) -> PluginBuild:
    """Create one minimal plugin/version/build chain for direct Job rows."""
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
        build_key="plugin_build_export_test",
        manifest_build_id="build-export-test",
        plugin_version=version,
        target_os="linux",
        target_arch="amd64",
        runtime_type="docker",
        python_version="3.12",
        sdk_version="1.0",
        package_path="plugins/plugin_build_export_test",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/env.tar.zst",
        runtime_fingerprint="c" * 64,
        build_metadata_json={},
        status="ENABLED",
    )
    session.add_all([plugin, version, build])
    session.flush()
    return build


def test_audit_export_streams_header_and_time_ascending_rows(client: TestClient) -> None:
    """The export must start with the exact header and stream oldest entries first."""
    upload = client.post("/api/v1/files", files={"file": ("a.nc", b"abc")})
    assert upload.status_code == 201
    with client.app.state.session_factory() as session:
        session.add(
            AuditLogRecord(
                at=datetime(2026, 1, 1, tzinfo=UTC),
                actor_type="user",
                actor_id=7,
                actor_name="=pwned()",
                action="file.upload",
                resource_type="file",
                resource_id="@hub",
                ip="10.0.0.9",
                result="ok",
            )
        )
        session.add(
            AuditLogRecord(
                at=datetime(2026, 1, 2, tzinfo=UTC),
                actor_type="user",
                actor_id=7,
                actor_name="admin",
                action="user.create",
                resource_type="user",
                resource_id="-9",
                ip="10.0.0.9",
                result="ok",
            )
        )
        session.commit()

    response = client.get("/api/v1/audit-logs/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="audit-logs.csv"'
    assert response.text.splitlines()[0] == (
        "at,actor_type,actor_name,action,resource_type,resource_id,result,ip"
    )
    rows = list(csv.reader(io.StringIO(response.text)))
    assert len(rows) == 4  # header + 2 seeded + 1 upload audit
    assert [row[3] for row in rows[1:]] == ["file.upload", "user.create", "file.upload"]
    # Cells starting with =, +, -, @ are neutralised with a leading apostrophe.
    assert rows[1][2] == "'=pwned()"
    assert rows[1][5] == "'@hub"
    assert rows[2][5] == "'-9"


def test_jobs_export_returns_row_count_matching_jobs(client: TestClient) -> None:
    """The export must carry one newest-first row per Job with the documented columns."""
    with client.app.state.session_factory() as session:
        build = _seed_build(session)
        for index in range(3):
            session.add(
                Job(
                    plugin_build=build,
                    runtime_type="docker",
                    runtime_fingerprint="c" * 64,
                    status="FAILED" if index == 0 else "PENDING",
                    params_json={},
                    inputs_json={},
                    timeout_seconds=30,
                    created_at=datetime(2026, 1, 1 + index, tzinfo=UTC),
                    error_summary="=SUM(A1:A2)" if index == 0 else None,
                )
            )
        session.commit()

    response = client.get("/api/v1/jobs/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="jobs.csv"'
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == [
        "job_id",
        "plugin_id",
        "version",
        "runtime_type",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "error_summary",
    ]
    assert len(rows) == 4  # header + 3 jobs
    newest = rows[1]
    assert newest[0].startswith("job_")
    assert newest[1] == "nc_to_shp"
    assert newest[2] == "1.0.0"
    assert newest[3] == "docker"
    assert newest[4] == "PENDING"
    assert newest[8] == ""
    failed = rows[3]
    assert failed[4] == "FAILED"
    assert failed[8] == "'=SUM(A1:A2)"


def test_exports_reject_anonymous_clients(tmp_path: Path) -> None:
    """Without credentials both exports must return the stable auth-required error."""
    with TestClient(create_app(_settings(tmp_path, "required"))) as required_client:
        audit_response = required_client.get("/api/v1/audit-logs/export")
        jobs_response = required_client.get("/api/v1/jobs/export")

        assert audit_response.status_code == 401
        assert audit_response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert jobs_response.status_code == 401
        assert jobs_response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_jobs_export_requires_operator_role(tmp_path: Path) -> None:
    """Roles below operator must not read the Job export."""
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

        denied = required_client.get("/api/v1/jobs/export", headers=viewer_headers)

        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"


def test_audit_export_requires_admin_role(tmp_path: Path) -> None:
    """The audit export must sit behind the same admin gate as the audit list."""
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
            json={"username": "plain_operator", "password": "long-enough-pass", "role": "operator"},
            headers=admin_headers,
        )
        operator_login = required_client.post(
            "/api/v1/auth/login",
            json={"username": "plain_operator", "password": "long-enough-pass"},
        )
        operator_headers = {"Authorization": f"Bearer {operator_login.json()['token']}"}

        denied = required_client.get("/api/v1/audit-logs/export", headers=operator_headers)
        allowed = required_client.get("/api/v1/audit-logs/export", headers=admin_headers)

        assert denied.status_code == 403
        assert allowed.status_code == 200
