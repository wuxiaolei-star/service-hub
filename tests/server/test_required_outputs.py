"""G6 (B5) required-output enforcement on the runner completion path.

A SUCCESS completion reported by a runner must satisfy the Build manifest's
output contract: every required ``file``/``files`` output needs a registered
file, otherwise the job is flipped to FAILED with ``PLUGIN_OUTPUT_MISSING``.
``object`` outputs are explicitly exempt (they never produce a file; manifest
v1.0 defines no server-side contract for ``PluginResult.data``).
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
from python_hub_contracts import load_plugin_manifest
from sqlalchemy import select

_FIXTURE_OUTPUT_FILE = {"name": "result_files", "label": "Result", "type": "file", "required": True}
_OBJECT_OUTPUT = {"name": "summary", "label": "Summary", "type": "object", "required": True}


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


def test_missing_required_file_output_fails_job(client: TestClient) -> None:
    """A SUCCESS report without the required file must not stand as SUCCESS."""
    _seed_build(client, outputs=[_FIXTURE_OUTPUT_FILE])
    job_key = _create_job(client)
    _claim(client, job_key)

    completion = _complete_success(client, job_key, files=[])

    assert completion.status_code == 200, completion.text
    assert completion.json() == {"id": job_key, "status": "FAILED"}
    status, error_summary = _job_state(client, job_key)
    assert status == "FAILED"
    assert error_summary == "PLUGIN_OUTPUT_MISSING: result_files"


def test_registered_required_file_output_stays_success(client: TestClient) -> None:
    """Reporting the required output keeps the SUCCESS terminal state intact."""
    _seed_build(client, outputs=[_FIXTURE_OUTPUT_FILE])
    job_key = _create_job(client)
    _claim(client, job_key)
    _write_output(client, job_key, "result.zip", b"zip-bytes")

    completion = _complete_success(
        client,
        job_key,
        files=[_reported_file("result_files", "result.zip", len(b"zip-bytes"))],
    )

    assert completion.status_code == 200, completion.text
    assert completion.json() == {"id": job_key, "status": "SUCCESS"}
    status, error_summary = _job_state(client, job_key)
    assert status == "SUCCESS"
    assert error_summary is None
    outputs = client.get(f"/api/v1/jobs/{job_key}/outputs")
    assert outputs.status_code == 200
    assert [item["name"] for item in outputs.json()["items"]] == ["result_files"]


def test_required_object_output_is_exempt_from_file_registration(
    client: TestClient,
) -> None:
    """Object outputs never produce files; their absence must not fail a job."""
    _seed_build(client, outputs=[_OBJECT_OUTPUT])
    job_key = _create_job(client)
    _claim(client, job_key)

    completion = _complete_success(client, job_key, files=[])

    assert completion.status_code == 200, completion.text
    assert completion.json() == {"id": job_key, "status": "SUCCESS"}
    status, error_summary = _job_state(client, job_key)
    assert status == "SUCCESS"
    assert error_summary is None


def test_missing_optional_file_output_stays_success(client: TestClient) -> None:
    """Only required outputs are enforced; optional outputs stay optional."""
    _seed_build(
        client,
        outputs=[{**_FIXTURE_OUTPUT_FILE, "required": False}],
    )
    job_key = _create_job(client)
    _claim(client, job_key)

    completion = _complete_success(client, job_key, files=[])

    assert completion.json() == {"id": job_key, "status": "SUCCESS"}


def test_missing_required_output_lists_every_missing_name(client: TestClient) -> None:
    """The error summary carries the full missing list, not just the first hit."""
    _seed_build(
        client,
        outputs=[
            {"name": "result_files", "label": "Result", "type": "file", "required": True},
            {"name": "extra_bundle", "label": "Bundle", "type": "files", "required": True},
        ],
    )
    job_key = _create_job(client)
    _claim(client, job_key)

    _complete_success(client, job_key, files=[])

    status, error_summary = _job_state(client, job_key)
    assert status == "FAILED"
    assert error_summary == "PLUGIN_OUTPUT_MISSING: result_files, extra_bundle"


def test_nc_to_shp_result_archive_compatibility(client: TestClient) -> None:
    """The real nc_to_shp 1.0.1 contract: reporting result_archive keeps SUCCESS."""
    manifest = load_plugin_manifest(
        Path(__file__).parents[2] / "packages/nc-to-shp-plugin/plugin.yaml"
    ).model_dump(mode="json")
    assert manifest["outputs"] == [
        {"name": "result_archive", "label": "Shapefile ZIP", "type": "file", "required": True}
    ]
    _seed_build(client, manifest=manifest)  # type: ignore[arg-type]
    job_key = _create_job(client)
    _claim(client, job_key)
    _write_output(client, job_key, "result.zip", b"shapefile-archive")

    completion = _complete_success(
        client,
        job_key,
        files=[_reported_file("result_archive", "result.zip", len(b"shapefile-archive"))],
    )

    assert completion.json() == {"id": job_key, "status": "SUCCESS"}
    assert _job_state(client, job_key)[0] == "SUCCESS"


def test_nc_to_shp_missing_result_archive_fails(client: TestClient) -> None:
    """Without result_archive the same real contract turns SUCCESS into FAILED."""
    manifest = load_plugin_manifest(
        Path(__file__).parents[2] / "packages/nc-to-shp-plugin/plugin.yaml"
    ).model_dump(mode="json")
    _seed_build(client, manifest=manifest)  # type: ignore[arg-type]
    job_key = _create_job(client)
    _claim(client, job_key)

    completion = _complete_success(client, job_key, files=[])

    assert completion.json() == {"id": job_key, "status": "FAILED"}
    status, error_summary = _job_state(client, job_key)
    assert status == "FAILED"
    assert error_summary == "PLUGIN_OUTPUT_MISSING: result_archive"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(outputs: list[dict[str, object]]) -> dict[str, object]:
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
        "outputs": outputs,
        "execution": {"timeout": 30, "concurrency": 1},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }


def _seed_build(
    client: TestClient,
    *,
    outputs: list[dict[str, object]] | None = None,
    manifest: dict[str, object] | None = None,
) -> None:
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=manifest if manifest is not None else _manifest(outputs or []),
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


def _create_job(client: TestClient) -> str:
    uploaded = client.post(
        "/api/v1/files",
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = str(uploaded.json()["file_id"])
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": "nc_to_shp",
            "version": "1.0.0",
            "runtime_type": "docker",
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["job_id"])


def _claim(client: TestClient, job_key: str) -> None:
    claim = client.post(
        "/internal/v1/jobs/claim",
        headers={"X-Hub-Runner-Token": "runner-test-secret"},
        json={"runtime_type": "docker"},
    )
    assert claim.status_code == 200, claim.text
    assert claim.json()["job_id"] == job_key


def _write_output(client: TestClient, job_key: str, name: str, payload: bytes) -> None:
    output = client.app.state.storage.open_relative(f"jobs/{job_key}/output/{name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)


def _reported_file(name: str, path: str, size: int) -> dict[str, object]:
    return {
        "name": name,
        "path": path,
        "format": "zip",
        "size": size,
        "sha256": "0" * 64,
    }


def _complete_success(
    client: TestClient,
    job_key: str,
    *,
    files: list[dict[str, object]],
) -> object:
    now = datetime.now(UTC).isoformat()
    return client.post(
        f"/internal/v1/jobs/{job_key}/complete",
        headers={"X-Hub-Runner-Token": "runner-test-secret"},
        json={
            "runtime_type": "docker",
            "exit_code": 0,
            "result": {
                "protocol_version": "1.0",
                "job_id": job_key,
                "status": "SUCCESS",
                "started_at": now,
                "finished_at": now,
                "duration_ms": 1,
                "message": "ok",
                "data": {},
                "files": files,
                "error": None,
            },
        },
    )


def _job_state(client: TestClient, job_key: str) -> tuple[str, str | None]:
    """Return (status, error_summary) for the job, read inside its session."""
    with client.app.state.session_factory() as session:
        job = session.scalar(select(Job).where(Job.job_key == job_key))
        assert job is not None
        return job.status, job.error_summary
