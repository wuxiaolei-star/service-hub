"""Deprecate endpoint: one-way Build status transition with audit coverage."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Environment, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
    auth=AuthSettings(mode="off"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_ready_build_can_be_deprecated_and_stays_visible(client: TestClient) -> None:
    """Deprecation marks the Build without hiding it from the registry."""
    build_key = _seed_build(client, status="READY")

    response = client.post(f"/api/v1/plugin-builds/{build_key}/deprecate")

    assert response.status_code == 200
    assert response.json()["status"] == "DEPRECATED"
    detail = client.get(f"/api/v1/plugin-builds/{build_key}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "DEPRECATED"
    listed = client.get("/api/v1/plugin-builds", params={"plugin_id": "nc_to_shp"})
    assert [item["build_id"] for item in listed.json()["items"]] == [build_key]
    with client.app.state.session_factory() as session:
        entry = session.query(AuditLogRecord).filter_by(action="plugin_build.deprecate").one()
    assert entry.resource_id == build_key
    assert entry.result == "ok"


def test_enabled_build_can_be_deprecated(client: TestClient) -> None:
    """Deprecation is the guarded way to retire a Build that served Jobs."""
    build_key = _seed_build(client, status="ENABLED")

    response = client.post(f"/api/v1/plugin-builds/{build_key}/deprecate")

    assert response.status_code == 200
    assert response.json()["status"] == "DEPRECATED"


@pytest.mark.parametrize("status", ["INSTALLING", "FAILED", "DEPRECATED"])
def test_deprecation_rejects_non_ready_states(client: TestClient, status: str) -> None:
    """Only READY and ENABLED Builds may enter the one-way DEPRECATED state."""
    build_key = _seed_build(client, status=status)

    response = client.post(f"/api/v1/plugin-builds/{build_key}/deprecate")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_DEPRECATABLE"


def test_deprecated_build_cannot_be_re_enabled(client: TestClient) -> None:
    """DEPRECATED is one-way: enable refuses it, so no silent revival exists."""
    build_key = _seed_build(client, status="READY")
    client.post(f"/api/v1/plugin-builds/{build_key}/deprecate")

    response = client.post(f"/api/v1/plugin-builds/{build_key}/enable")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_READY"
    still = client.get(f"/api/v1/plugin-builds/{build_key}")
    assert still.json()["status"] == "DEPRECATED"


def test_jobs_on_deprecated_build_are_rejected(client: TestClient) -> None:
    """Job resolution only accepts ENABLED Builds, so deprecation stops new work."""
    build_key = _seed_build(client, status="ENABLED")
    client.post(f"/api/v1/plugin-builds/{build_key}/deprecate")

    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": "conda-pack",
            "inputs": {},
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_ENABLED"


def test_unknown_build_key_deprecation_is_not_found(client: TestClient) -> None:
    response = client.post("/api/v1/plugin-builds/plugin_build_missing/deprecate")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_FOUND"


def _seed_build(client: TestClient, *, status: str) -> str:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        plugin_version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"plugin": {"id": "nc_to_shp", "version": "1.0.0"}},
            status="INSTALLED",
        )
        build = PluginBuild(
            build_key="plugin_build_nc_to_shp_conda_pack",
            manifest_build_id="build-conda-pack",
            plugin_version=plugin_version,
            target_os="linux",
            target_arch="amd64",
            runtime_type="conda-pack",
            python_version="3.12",
            sdk_version="1.0",
            package_path="plugins/plugin_build_nc_to_shp_conda_pack",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint="c" * 64,
            build_metadata_json={
                "schema_version": "1.0",
                "runtime": {
                    "type": "conda-pack",
                    "archive": "runtime/env.tar.zst",
                    "fingerprint": "c" * 64,
                },
            },
            status=status,
        )
        environment = Environment(
            plugin_build=build,
            runtime_type="conda-pack",
            fingerprint="c" * 64,
            metadata_json={"type": "conda-pack"},
            status=status,
        )
        session.add_all([plugin, plugin_version, build, environment])
        session.commit()
        return build.build_key
