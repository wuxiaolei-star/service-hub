"""G5(a) (B5) per-build concurrency gate on Job creation.

``JobService.create`` counts PENDING/PREPARING/RUNNING jobs of the resolved
build against the manifest's ``execution.concurrency`` and rejects further
creation with 409 ``PLUGIN_CONCURRENCY_LIMIT``. The gate applies to every
actor: the public API and the scheduler (which funnels through ``create``)
are both covered, with no bypass point.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from sqlalchemy import select


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


def test_second_active_job_on_concurrency_one_build_is_rejected(
    client: TestClient,
) -> None:
    """A saturated build must refuse further jobs with the stable 409 code."""
    _seed_build(client, concurrency=1)
    _create_job(client)  # occupies the single slot as PENDING

    second = _create_job_response(client)

    assert second.status_code == 409, second.text
    body = second.json()["error"]
    assert body["code"] == "PLUGIN_CONCURRENCY_LIMIT"
    assert body["details"]["build_key"] == "plugin_build_docker"
    assert body["details"]["active"] == 1
    assert body["details"]["limit"] == 1


def test_terminal_job_frees_the_build_slot(client: TestClient) -> None:
    """Finishing the running job restores the ability to create new work."""
    _seed_build(client, concurrency=1)
    first = _create_job(client)
    _set_job_status(client, first, "SUCCESS")

    second = _create_job_response(client)

    assert second.status_code == 201, second.text
    assert second.json()["status"] == "PENDING"


def test_declared_concurrency_admits_that_many_parallel_jobs(
    client: TestClient,
) -> None:
    """A build declaring concurrency 2 runs two jobs and refuses the third."""
    _seed_build(client, concurrency=2)
    _create_job(client)
    _create_job(client)

    third = _create_job_response(client)

    assert third.status_code == 409, third.text
    assert third.json()["error"]["code"] == "PLUGIN_CONCURRENCY_LIMIT"
    assert third.json()["error"]["details"]["active"] == 2


def test_cancelled_job_frees_the_build_slot(client: TestClient) -> None:
    """A cancelled job left the count set, so a new job can be created."""
    _seed_build(client, concurrency=1)
    first = _create_job(client)
    _set_job_status(client, first, "CANCELLED")

    second = _create_job_response(client)

    assert second.status_code == 201, second.text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_build(client: TestClient, *, concurrency: int) -> None:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=_manifest(concurrency),
            status="INSTALLED",
        )
        fingerprint = "d" * 64
        build = PluginBuild(
            build_key="plugin_build_docker",
            manifest_build_id="build-docker",
            plugin_version=version,
            target_os="linux",
            target_arch="amd64",
            runtime_type="docker",
            python_version="3.12",
            sdk_version="1.0",
            package_path="plugins/plugin_build_docker",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="image.tar.zst",
            runtime_fingerprint=fingerprint,
            build_metadata_json={
                "schema_version": "1.0",
                "build_id": "build-docker",
                "plugin_id": "nc_to_shp",
                "plugin_version": "1.0.0",
                "target": {"os": "linux", "arch": "amd64"},
                "python_version": "3.12",
                "runtime": {
                    "type": "docker",
                    "archive": "image.tar.zst",
                    "image": "nc",
                    "digest": fingerprint,
                },
                "sdk_version": "1.0.0",
                "source_sha256": "a" * 64,
                "built_at": datetime.now(UTC).isoformat(),
            },
            status="ENABLED",
        )
        session.add_all([plugin, version, build])
        session.commit()


def _manifest(concurrency: int) -> dict[str, object]:
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
        "outputs": [],
        "execution": {"timeout": 30, "concurrency": concurrency},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }


def _create_job(client: TestClient) -> str:
    created = _create_job_response(client)
    assert created.status_code == 201, created.text
    return str(created.json()["job_id"])


def _create_job_response(client: TestClient) -> object:
    uploaded = client.post(
        "/api/v1/files",
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = str(uploaded.json()["file_id"])
    return client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": "docker",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )


def _set_job_status(client: TestClient, job_key: str, job_status: str) -> None:
    with client.app.state.session_factory() as session:
        job = session.scalar(select(Job).where(Job.job_key == job_key))
        assert job is not None
        job.status = job_status
        session.commit()
