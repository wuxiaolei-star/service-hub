from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Environment, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
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
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_ready_build_can_be_enabled_and_listed(client: TestClient) -> None:
    build_key = _seed_build(client, runtime_type="conda-pack", status="READY")

    enabled = client.post(f"/api/v1/plugin-builds/{build_key}/enable")

    assert enabled.status_code == 200
    assert enabled.json()["status"] == "ENABLED"
    assert enabled.json()["runtime_type"] == "conda-pack"
    listed = client.get("/api/v1/plugins")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == "nc_to_shp"


def test_installing_build_cannot_be_enabled(client: TestClient) -> None:
    build_key = _seed_build(client, runtime_type="docker", status="INSTALLING")

    response = client.post(f"/api/v1/plugin-builds/{build_key}/enable")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_READY"


def _seed_build(client: TestClient, *, runtime_type: str, status: str) -> str:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=_manifest(),
            status="INSTALLED",
        )
        runtime_metadata = (
            {"type": "conda-pack", "archive": "runtime/env.tar.zst", "fingerprint": "c" * 64}
            if runtime_type == "conda-pack"
            else {"type": "docker", "archive": "image.tar.zst", "image": "nc", "digest": "d" * 64}
        )
        fingerprint = runtime_metadata.get("fingerprint") or runtime_metadata["digest"]
        build = PluginBuild(
            build_key=f"plugin_build_{runtime_type.replace('-', '_')}",
            manifest_build_id=f"build-{runtime_type}",
            plugin_version=version,
            target_os="linux",
            target_arch="amd64",
            runtime_type=runtime_type,
            python_version="3.12",
            sdk_version="1.0",
            package_path=f"plugins/plugin_build_{runtime_type}",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint=str(fingerprint),
            build_metadata_json={
                "schema_version": "1.0",
                "runtime": runtime_metadata,
            },
            status=status,
        )
        environment = Environment(
            plugin_build=build,
            runtime_type=runtime_type,
            fingerprint=str(fingerprint),
            image_digest=str(fingerprint) if runtime_type == "docker" else None,
            metadata_json=runtime_metadata,
            status=status,
        )
        session.add_all([plugin, version, build, environment])
        session.commit()
        return build.build_key


def _manifest() -> dict[str, object]:
    return {
        "spec_version": "1.0",
        "plugin": {"id": "nc_to_shp", "name": "NC to Shapefile", "version": "1.0.0"},
        "sdk": {"version": "1.0"},
        "runtime": {"type": "process", "python": {"version": "3.12"}},
        "entrypoint": {"module": "nc_to_shp_plugin.main", "function": "run"},
        "parameters": [],
        "inputs": [
            {
                "name": "source_nc",
                "label": "Source NC",
                "type": "file",
                "required": True,
                "extensions": [".nc"],
            }
        ],
        "outputs": [{"name": "result_files", "label": "Result", "type": "file", "required": True}],
        "execution": {"timeout": 30, "concurrency": 1},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }
