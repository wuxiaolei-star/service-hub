"""Behavior tests for immutable Job workspaces and output registration."""

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hub_server.errors import HubError
from hub_server.models import FileRecord, Job, JobFile, Plugin, PluginBuild, PluginVersion
from hub_server.services.workspaces import JobWorkspaceService
from hub_server.storage import LocalStorage
from python_hub_contracts import JobRuntimeSpec
from sqlalchemy import select
from sqlalchemy.orm import Session


def _persist_job_with_input(
    session: Session, storage: LocalStorage, *, status: str = "AVAILABLE"
) -> tuple[Job, FileRecord]:
    file_key = "file_123e4567e89b42d3a456426614174000"
    stored = storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)
    input_file = FileRecord(
        file_key=file_key,
        scope="UPLOAD",
        role="INPUT",
        logical_name="source.nc",
        original_filename="source.nc",
        relative_path=stored.relative_path,
        extension=".nc",
        mime_type="application/x-netcdf",
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        status=status,
    )
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
        plugin_version=version,
        build_key="plugin_build_123e4567e89b42d3a456426614174000",
        manifest_build_id="publisher-build",
        target_os="linux",
        target_arch="amd64",
        runtime_type="docker",
        python_version="3.10",
        sdk_version="1.0.0",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="image.tar.zst",
        runtime_fingerprint="c" * 64,
        build_metadata_json={},
        status="ENABLED",
    )
    job = Job(
        job_key="job_123e4567e89b42d3a456426614174000",
        plugin_build=build,
        runtime_type="docker",
        runtime_fingerprint="c" * 64,
        status="PENDING",
        params_json={"group_name": "1"},
        inputs_json={"source_nc": file_key},
        timeout_seconds=60,
        created_at=datetime(2026, 9, 6, 12, tzinfo=UTC),
    )
    session.add_all([job, input_file])
    session.commit()
    return job, input_file


def test_workspace_copies_input_and_serializes_runtime_spec(
    tmp_path: Path, session: Session
) -> None:
    """Missing immutable input bytes or an ad-hoc job.json makes runs irreproducible."""
    storage = LocalStorage(tmp_path / "data")
    job, input_file = _persist_job_with_input(session, storage)

    workspace = JobWorkspaceService(storage).prepare(job, [input_file])

    assert workspace == tmp_path / "data" / "jobs" / job.job_key
    assert (workspace / "input" / input_file.logical_name).read_bytes() == b"netcdf"
    assert {path.name for path in workspace.iterdir()} == {
        "input",
        "work",
        "output",
        "logs",
        "job.json",
    }
    runtime = JobRuntimeSpec.model_validate_json((workspace / "job.json").read_text("utf-8"))
    runtime_input = runtime.inputs["source_nc"]
    assert not isinstance(runtime_input, list)
    assert runtime_input.id == input_file.file_key
    assert runtime_input.path == f"input/{input_file.logical_name}"
    assert runtime.plugin.build_id == job.plugin_build.build_key
    assert runtime.directories.model_dump() == {
        "input": "input",
        "work": "work",
        "output": "output",
        "logs": "logs",
    }
    assert job.workspace_path == f"jobs/{job.job_key}"
    assert job.job_json == json.loads((workspace / "job.json").read_text("utf-8"))


def test_workspace_rejects_unavailable_input_without_leaving_job_directory(
    tmp_path: Path, session: Session
) -> None:
    """Copying a non-available payload bypasses the FileRecord lifecycle."""
    storage = LocalStorage(tmp_path / "data")
    job, input_file = _persist_job_with_input(session, storage, status="DELETED")

    with pytest.raises(HubError) as raised:
        JobWorkspaceService(storage).prepare(job, [input_file])

    assert raised.value.code == "FILE_NOT_FOUND"
    assert not (tmp_path / "data" / "jobs" / job.job_key).exists()


def test_workspace_rejects_input_payload_that_no_longer_matches_metadata(
    tmp_path: Path, session: Session
) -> None:
    """A changed upload must not be snapshotted under its stale integrity metadata."""
    storage = LocalStorage(tmp_path / "data")
    job, input_file = _persist_job_with_input(session, storage)
    storage.open_relative(input_file.relative_path).write_bytes(b"tampered")

    with pytest.raises(HubError) as raised:
        JobWorkspaceService(storage).prepare(job, [input_file])

    assert raised.value.code == "FILE_INTEGRITY_MISMATCH"
    assert not (tmp_path / "data" / "jobs" / job.job_key).exists()


def test_workspace_rejects_output_escape(tmp_path: Path) -> None:
    """A runner-controlled output path must never resolve outside output/."""
    storage = LocalStorage(tmp_path / "data")
    workspace = storage.open_relative("jobs/job_123e4567e89b42d3a456426614174000")
    (workspace / "output").mkdir(parents=True)

    with pytest.raises(ValueError):
        JobWorkspaceService(storage).resolve_output(workspace, "../secret.zip")


def test_register_output_hashes_file_before_inserting_metadata(
    tmp_path: Path, session: Session
) -> None:
    """Registering before hashing can commit metadata for different output bytes."""
    storage = LocalStorage(tmp_path / "data")
    job, input_file = _persist_job_with_input(session, storage)
    service = JobWorkspaceService(storage)
    workspace = service.prepare(job, [input_file])
    output = workspace / "output" / "result.zip"
    output.write_bytes(b"zip bytes")

    record = service.register_output(
        job,
        logical_name="result_files",
        relative_path="result.zip",
        mime_type="application/zip",
    )

    assert record.file_key.startswith("file_")
    assert record.scope == "JOB"
    assert record.role == "OUTPUT"
    assert record.logical_name == "result_files"
    assert record.original_filename == "result.zip"
    assert record.relative_path == f"jobs/{job.job_key}/output/result.zip"
    assert record.extension == ".zip"
    assert record.size_bytes == len(b"zip bytes")
    assert record.sha256 == hashlib.sha256(b"zip bytes").hexdigest()
    association = session.scalar(select(JobFile).where(JobFile.file_record_id == record.id))
    assert association is not None
    assert association.job_id == job.id
    assert association.role == "OUTPUT"
    assert association.logical_name == "result_files"
    assert association.file_snapshot_json["sha256"] == record.sha256


def test_register_output_rejects_missing_file_before_database_insert(
    tmp_path: Path, session: Session
) -> None:
    """A missing runner output cannot become an AVAILABLE FileRecord."""
    storage = LocalStorage(tmp_path / "data")
    job, input_file = _persist_job_with_input(session, storage)
    service = JobWorkspaceService(storage)
    service.prepare(job, [input_file])

    with pytest.raises(HubError) as raised:
        service.register_output(job, logical_name="result_files", relative_path="missing.zip")

    assert raised.value.code == "JOB_OUTPUT_NOT_FOUND"
    assert session.scalar(select(FileRecord).where(FileRecord.scope == "JOB")) is None
