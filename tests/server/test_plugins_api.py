from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_publisher.archive import create_plugin_package
from hub_publisher.builder import create_build_manifest
from hub_publisher.project import PluginProject
from hub_server.main import create_app
from hub_server.models import Environment, Plugin, PluginBuild, PluginVersion
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


def test_plugin_detail_exposes_version_manifest_without_private_build_paths(
    client: TestClient, tmp_path: Path
) -> None:
    """Removing the public detail read model would hide the manifest needed to create jobs."""
    _install_plugin(client, tmp_path)

    detail = client.get("/api/v1/plugins/nc_to_shp")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["id"] == "nc_to_shp"
    assert payload["versions"][0]["version"] == "1.0.0"
    assert payload["versions"][0]["manifest"]["entrypoint"]["function"] == "run"
    assert "package_path" not in payload
    assert "runtime_archive_path" not in payload
    assert "metadata_json" not in payload


def test_unknown_plugin_detail_uses_plugin_not_found_error(client: TestClient) -> None:
    """Treating a missing plugin as a framework route miss would break the stable API error."""
    response = client.get("/api/v1/plugins/unknown")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_NOT_FOUND"


def test_plugin_build_list_filters_to_requested_plugin(client: TestClient) -> None:
    """Dropping the joined plugin filter would leak Builds from unrelated plugins."""
    nc_build_key = _seed_build(client, runtime_type="conda-pack", status="READY")
    _seed_build(
        client,
        runtime_type="docker",
        status="READY",
        plugin_id="other_plugin",
        version="2.0.0",
    )

    builds = client.get("/api/v1/plugin-builds", params={"plugin_id": "nc_to_shp"})

    assert builds.status_code == 200
    assert [item["build_id"] for item in builds.json()["items"]] == [nc_build_key]
    assert builds.json()["items"][0]["plugin_id"] == "nc_to_shp"


def test_plugin_build_list_is_capped_at_100_records(client: TestClient) -> None:
    """Removing the query cap would let an unbounded Build history reach the console."""
    for index in range(101):
        _seed_build(
            client,
            runtime_type="conda-pack",
            status="READY",
            plugin_id=f"plugin_{index}",
        )

    builds = client.get("/api/v1/plugin-builds")

    assert builds.status_code == 200
    assert len(builds.json()["items"]) == 100


def test_plugin_build_list_orders_newest_first_then_build_key_descending(
    client: TestClient,
) -> None:
    """Removing either order clause would make console Build ordering nondeterministic."""
    tie_timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    _seed_build(
        client,
        runtime_type="conda-pack",
        status="READY",
        plugin_id="older",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    first_tie = _seed_build(
        client,
        runtime_type="conda-pack",
        status="READY",
        plugin_id="tie_a",
        created_at=tie_timestamp,
    )
    second_tie = _seed_build(
        client,
        runtime_type="conda-pack",
        status="READY",
        plugin_id="tie_z",
        created_at=tie_timestamp,
    )

    builds = client.get("/api/v1/plugin-builds")

    assert builds.status_code == 200
    assert [item["build_id"] for item in builds.json()["items"]] == [
        second_tie,
        first_tie,
        "plugin_build_older_conda_pack",
    ]


def test_plugin_detail_orders_versions_descending(client: TestClient) -> None:
    """Ascending version iteration would show stale plugin configuration first."""
    _seed_plugin_version(client, version="1.0.0")
    _seed_plugin_version(client, version="2.0.0")

    detail = client.get("/api/v1/plugins/nc_to_shp")

    assert detail.status_code == 200
    assert [version["version"] for version in detail.json()["versions"]] == ["2.0.0", "1.0.0"]


def _seed_build(
    client: TestClient,
    *,
    runtime_type: str,
    status: str,
    plugin_id: str = "nc_to_shp",
    version: str = "1.0.0",
    created_at: datetime | None = None,
) -> str:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key=plugin_id, name=plugin_id)
        plugin_version = PluginVersion(
            plugin=plugin,
            version=version,
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
            build_key=f"plugin_build_{plugin_id}_{runtime_type.replace('-', '_')}",
            manifest_build_id=f"build-{runtime_type}",
            plugin_version=plugin_version,
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
            created_at=created_at or datetime.now(UTC),
        )
        environment = Environment(
            plugin_build=build,
            runtime_type=runtime_type,
            fingerprint=str(fingerprint),
            image_digest=str(fingerprint) if runtime_type == "docker" else None,
            metadata_json=runtime_metadata,
            status=status,
        )
        session.add_all([plugin, plugin_version, build, environment])
        session.commit()
        return build.build_key


def _seed_plugin_version(client: TestClient, *, version: str) -> None:
    with client.app.state.session_factory() as session:
        plugin = session.query(Plugin).filter_by(plugin_key="nc_to_shp").one_or_none()
        if plugin is None:
            plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
            session.add(plugin)
        session.add(
            PluginVersion(
                plugin=plugin,
                version=version,
                spec_version="1.0",
                sdk_version="1.0",
                source_sha256="a" * 64,
                manifest_json=_manifest(),
                status="INSTALLED",
            )
        )
        session.commit()


def _install_plugin(client: TestClient, tmp_path: Path) -> None:
    """Create one valid package and register it through the public install endpoint."""
    project_root = tmp_path / "plugin"
    source = project_root / "src" / "nc_to_shp_plugin"
    source.mkdir(parents=True)
    (project_root / "plugin.yaml").write_text(_manifest_yaml(), encoding="utf-8")
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "main.py").write_text("def run():\n    return None\n", encoding="utf-8")
    runtime_archive = tmp_path / "runtime.tar.zst"
    runtime_archive.write_bytes(b"test runtime")
    project = PluginProject.load(project_root)
    build = create_build_manifest(
        project,
        "conda-pack",
        "amd64",
        runtime_archive,
        docker_digest=None,
        source_date_epoch=0,
    )
    package = create_plugin_package(project, build, runtime_archive, tmp_path / "dist", 0)

    response = client.post(
        "/api/v1/plugins/install",
        files={"file": (package.name, package.read_bytes(), "application/octet-stream")},
    )

    assert response.status_code == 202, response.text


def _manifest_yaml() -> str:
    return """\
spec_version: "1.0"
plugin:
  id: nc_to_shp
  name: NC to Shapefile
  version: "1.0.0"
sdk:
  version: "1.0"
runtime:
  type: process
  python:
    version: "3.12"
entrypoint:
  module: nc_to_shp_plugin.main
  function: run
parameters: []
inputs: []
outputs: []
execution:
  timeout: 30
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
"""


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
