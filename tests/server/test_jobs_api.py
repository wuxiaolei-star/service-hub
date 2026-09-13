from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Environment, Job, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from python_hub_contracts import load_plugin_manifest


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


@pytest.mark.parametrize("runtime_type", ["docker", "conda-pack"])
@pytest.mark.parametrize("required", [False, True])
def test_real_nc_request_optional_null(
    client: TestClient, runtime_type: str, required: bool
) -> None:
    _seed_build(client, runtime_type=runtime_type)
    manifest = load_plugin_manifest(
        Path(__file__).parents[2] / "packages/nc-to-shp-plugin/plugin.yaml"
    ).model_dump(mode="json")
    for parameter in manifest["parameters"]:
        if parameter["name"] == "target_crs":
            parameter["required"] = required
    with client.app.state.session_factory() as session:
        version = session.query(PluginVersion).one()
        version.manifest_json = manifest
        session.commit()
    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": runtime_type,
            "inputs": {"source_nc": _upload_nc(client)},
            "params": {
                "group_name": "1",
                "metrics": ["depth", "stage"],
                "start_time": 1,
                "end_time": 20,
                "target_crs": None,
            },
        },
    )
    assert response.status_code == (422 if required else 201), response.text


def test_create_job_uses_explicit_enabled_conda_build(client: TestClient) -> None:
    _seed_build(client, runtime_type="conda-pack")
    file_id = _upload_nc(client)

    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": "conda-pack",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )

    assert response.status_code == 201
    assert response.json()["runtime_type"] == "conda-pack"
    assert response.json()["status"] == "PENDING"


def test_pending_job_detail_includes_unstarted_lifecycle_timestamps(client: TestClient) -> None:
    """Omitting lifecycle timestamps would hide an unstarted Job from the monitor."""
    _seed_build(client, runtime_type="docker")
    file_id = _upload_nc(client)
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )
    assert created.status_code == 201

    job = client.get(f"/api/v1/jobs/{created.json()['job_id']}")

    assert job.status_code == 200
    assert job.json()["created_at"].endswith("Z")
    assert job.json()["started_at"] is None
    assert job.json()["finished_at"] is None


def test_job_without_runtime_type_defaults_to_docker(client: TestClient) -> None:
    _seed_build(client, runtime_type="docker")
    file_id = _upload_nc(client)

    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )

    assert response.status_code == 201
    assert response.json()["runtime_type"] == "docker"


def test_create_job_accepts_declared_metrics_string_list(client: TestClient) -> None:
    """Treating metrics as a scalar would reject the authoritative nc_to_shp request."""
    _seed_build(client, runtime_type="docker", include_metrics=True)
    file_id = _upload_nc(client)

    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {"metrics": ["depth", "stage"]},
        },
    )

    assert response.status_code == 201, response.text
    with client.app.state.session_factory() as session:
        job = session.query(Job).filter_by(job_key=response.json()["job_id"]).one()
        assert job.params_json == {"metrics": ["depth", "stage"]}


@pytest.mark.parametrize(
    "metrics",
    [[], ["depth", 1], ["unknown"], ["depth", "depth"]],
)
def test_create_job_rejects_invalid_metrics_string_list(
    client: TestClient, metrics: object
) -> None:
    """Malformed, disallowed, empty, or duplicate metrics must not reach the plugin."""
    _seed_build(client, runtime_type="docker", include_metrics=True)
    file_id = _upload_nc(client)

    response = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {"metrics": metrics},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "JOB_REQUEST_INVALID"


def test_cancel_marks_request_without_completing_job(client: TestClient) -> None:
    _seed_build(client, runtime_type="docker")
    file_id = _upload_nc(client)
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    ).json()

    response = client.post(f"/api/v1/jobs/{created['job_id']}/cancel")

    assert response.status_code == 202
    assert response.json()["cancel_requested"] is True
    assert response.json()["status"] == "PENDING"


def test_successful_runner_completion_registers_outputs(client: TestClient) -> None:
    _seed_build(client, runtime_type="docker")
    file_id = _upload_nc(client)
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    ).json()
    claim = _runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "docker"}).json()
    with client.app.state.session_factory() as session:
        claimed_job = session.query(Job).filter_by(job_key=claim["job_id"]).one()
        assert claimed_job.status == "PREPARING"
    output = client.app.state.storage.open_relative(f"jobs/{created['job_id']}/output/result.zip")
    output.write_bytes(b"zip-bytes")
    now = datetime.now(UTC).isoformat()

    complete = _runner_post(
        client,
        f"/internal/v1/jobs/{claim['job_id']}/complete",
        {
            "runtime_type": "docker",
            "exit_code": 0,
            "result": {
                "protocol_version": "1.0",
                "job_id": claim["job_id"],
                "status": "SUCCESS",
                "started_at": now,
                "finished_at": now,
                "duration_ms": 1,
                "message": "ok",
                "data": {},
                "files": [
                    {
                        "name": "result_files",
                        "path": "result.zip",
                        "format": "zip",
                        "size": len(b"zip-bytes"),
                        "sha256": "0" * 64,
                    }
                ],
                "error": None,
            },
        },
    )

    assert complete.status_code == 200, complete.text
    outputs = client.get(f"/api/v1/jobs/{created['job_id']}/outputs")
    assert outputs.status_code == 200
    assert outputs.json()["items"][0]["name"] == "result_files"
    assert outputs.json()["items"][0]["extension"] == ".zip"


def _upload_nc(client: TestClient) -> str:
    response = client.post(
        "/api/v1/files",
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert response.status_code == 201
    return str(response.json()["file_id"])


def _runner_post(client: TestClient, path: str, payload: dict[str, object]) -> object:
    return client.post(
        path,
        headers={"X-Hub-Runner-Token": "runner-test-secret"},
        json=payload,
    )


def _seed_build(client: TestClient, *, runtime_type: str, include_metrics: bool = False) -> None:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=_manifest(include_metrics=include_metrics),
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
                "build_id": f"build-{runtime_type}",
                "plugin_id": "nc_to_shp",
                "plugin_version": "1.0.0",
                "target": {"os": "linux", "arch": "amd64"},
                "python_version": "3.12",
                "runtime": runtime_metadata,
                "sdk_version": "1.0.0",
                "source_sha256": "a" * 64,
                "built_at": datetime.now(UTC).isoformat(),
            },
            status="ENABLED",
        )
        environment = Environment(
            plugin_build=build,
            runtime_type=runtime_type,
            fingerprint=str(fingerprint),
            environment_path=(
                f"environments/plugin_build_{runtime_type.replace('-', '_')}"
                if runtime_type == "conda-pack"
                else None
            ),
            image_digest=str(fingerprint) if runtime_type == "docker" else None,
            metadata_json=runtime_metadata,
            status="READY",
        )
        session.add_all([plugin, version, build, environment])
        session.commit()


def _manifest(*, include_metrics: bool = False) -> dict[str, object]:
    parameters: list[dict[str, object]] = []
    if include_metrics:
        parameters.append(
            {
                "name": "metrics",
                "label": "Metrics",
                "type": "string_list",
                "required": False,
                "default": ["depth", "stage"],
                "options": ["depth", "stage"],
                "min": 1,
                "max": 2,
            }
        )
    return {
        "spec_version": "1.0",
        "plugin": {"id": "nc_to_shp", "name": "NC to Shapefile", "version": "1.0.0"},
        "sdk": {"version": "1.0"},
        "runtime": {"type": "process", "python": {"version": "3.12"}},
        "entrypoint": {"module": "nc_to_shp_plugin.main", "function": "run"},
        "parameters": parameters,
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
