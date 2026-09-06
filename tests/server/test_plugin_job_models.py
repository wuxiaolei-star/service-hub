"""Persistence tests for immutable plugin builds and runtime-scoped work queues."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hub_server.models import (
    Job,
    JobFile,
    Plugin,
    PluginBuild,
    PluginVersion,
    RunnerOperation,
)
from hub_server.repositories import HubRepository
from python_hub_contracts import JobStatus, PluginBuildManifest, load_plugin_manifest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _plugin_version() -> PluginVersion:
    plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
    return PluginVersion(
        plugin=plugin,
        version="1.0.0",
        spec_version="1.0",
        sdk_version="1.0",
        source_sha256="a" * 64,
        manifest_json={"plugin": {"id": "nc_to_shp", "version": "1.0.0"}},
        status="INSTALLED",
    )


def _build(runtime_type: str) -> PluginBuild:
    suffix = "c" if runtime_type == "conda-pack" else "d"
    return PluginBuild(
        plugin_version=_plugin_version(),
        manifest_build_id=f"package-build-{suffix}",
        target_os="linux",
        target_arch="amd64",
        runtime_type=runtime_type,
        python_version="3.10",
        sdk_version="1.0.0",
        package_sha256=suffix * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/archive.tar.zst",
        runtime_fingerprint=suffix * 64,
        build_metadata_json={"runtime": {"type": runtime_type}},
        status="INSTALLING",
    )


def _job(
    build: PluginBuild,
    *,
    status: str = "PENDING",
    created_at: datetime | None = None,
) -> Job:
    return Job(
        plugin_build=build,
        runtime_type=build.runtime_type,
        runtime_fingerprint=build.runtime_fingerprint,
        status=status,
        params_json={"start_time": 1},
        inputs_json={"source_nc": {"sha256": "f" * 64}},
        timeout_seconds=60,
        created_at=created_at or datetime.now(UTC),
    )


def test_migration_creates_plugin_job_tables_and_claim_indexes(session: Session) -> None:
    """Removing migration 0002 would leave runtime persistence unavailable at startup."""
    database = inspect(session.get_bind())
    assert {
        "plugins",
        "plugin_versions",
        "plugin_builds",
        "environments",
        "jobs",
        "job_files",
        "runner_operations",
    }.issubset(database.get_table_names())
    assert "ix_plugin_builds_runtime_type" in {
        index["name"] for index in database.get_indexes("plugin_builds")
    }
    assert "ix_jobs_status" in {index["name"] for index in database.get_indexes("jobs")}
    assert "ix_runner_operations_status" in {
        index["name"] for index in database.get_indexes("runner_operations")
    }


def test_build_identity_allows_two_runtime_types_but_not_duplicates(session: Session) -> None:
    """Dropping runtime type from, or removing, the build identity breaks dual-runtime installs."""
    conda = _build("conda-pack")
    docker = _build("docker")
    docker.plugin_version = conda.plugin_version
    session.add_all([conda, docker])
    session.commit()

    duplicate = _build("docker")
    duplicate.plugin_version = conda.plugin_version
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize(
    ("runtime_type", "expected_fingerprint", "expected_digest"),
    [
        (
            "conda-pack",
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            None,
        ),
        ("docker", "1" * 64, "1" * 64),
    ],
)
def test_create_build_installation_preserves_manifest_identity_and_runtime_metadata(
    session: Session,
    runtime_type: str,
    expected_fingerprint: str,
    expected_digest: str | None,
) -> None:
    """Conflating public IDs with package IDs or omitting runtime metadata loses provenance."""
    fixtures = Path(__file__).parents[1] / "fixtures"
    plugin_manifest = load_plugin_manifest(fixtures / "valid-plugin.yaml")
    build_data = json.loads((fixtures / "valid-build.json").read_text("utf-8"))
    build_data["target"]["arch"] = "amd64"
    if runtime_type == "docker":
        build_data["build_id"] = "package-docker-build"
        build_data["runtime"] = {
            "type": "docker",
            "archive": "runtime/image.tar.zst",
            "image": "nc-to-shp:1.0.0",
            "digest": "1" * 64,
        }
    build_manifest = PluginBuildManifest.model_validate(build_data)

    build = HubRepository(session).create_build_installation(
        plugin_manifest=plugin_manifest,
        build_manifest=build_manifest,
        package_sha256="b" * 64,
    )

    assert build.build_key.startswith("plugin_build_")
    assert len(build.build_key) == len("plugin_build_") + 32
    assert build.manifest_build_id == build_manifest.build_id
    assert build.runtime_type == runtime_type
    assert build.build_metadata_json == build_manifest.model_dump(mode="json")
    assert build.plugin_version.manifest_json == plugin_manifest.model_dump(mode="json")
    assert build.environment is not None
    assert build.environment.fingerprint == expected_fingerprint
    assert build.environment.image_digest == expected_digest
    operation = session.scalar(
        select(RunnerOperation).where(RunnerOperation.plugin_build_id == build.id)
    )
    assert operation is not None
    assert operation.operation_key.startswith("operation_")
    assert operation.status == "PENDING"
    assert operation.runtime_type == runtime_type


def test_claim_job_only_claims_oldest_matching_pending_runtime(session: Session) -> None:
    """A non-atomic or unfiltered claim can hand a worker the wrong or newer runtime job."""
    now = datetime.now(UTC)
    conda_build = _build("conda-pack")
    docker_build = _build("docker")
    docker_build.plugin_version = conda_build.plugin_version
    older_conda = _job(conda_build, created_at=now - timedelta(seconds=2))
    newer_conda = _job(conda_build, created_at=now - timedelta(seconds=1))
    docker_job = _job(docker_build, created_at=now)
    running_conda = _job(conda_build, status="RUNNING", created_at=now - timedelta(seconds=3))
    session.add_all([newer_conda, older_conda, docker_job, running_conda])
    session.commit()

    claimed = HubRepository(session).claim_job("conda-pack")

    assert claimed is not None and claimed.job_key == older_conda.job_key
    assert claimed.status == "PREPARING"
    assert newer_conda.status == "PENDING"
    assert docker_job.status == "PENDING"
    assert running_conda.status == "RUNNING"


def test_claim_operation_only_claims_oldest_matching_pending_runtime(session: Session) -> None:
    """Installation workers must not consume another runtime's operation."""
    now = datetime.now(UTC)
    conda_build = _build("conda-pack")
    docker_build = _build("docker")
    docker_build.plugin_version = conda_build.plugin_version
    conda_operation = RunnerOperation(
        plugin_build=conda_build,
        runtime_type="conda-pack",
        kind="INSTALL",
        status="PENDING",
        payload_json={"archive": "runtime/env.tar.zst"},
        created_at=now - timedelta(seconds=1),
    )
    docker_operation = RunnerOperation(
        plugin_build=docker_build,
        runtime_type="docker",
        kind="INSTALL",
        status="PENDING",
        payload_json={"archive": "runtime/image.tar.zst"},
        created_at=now,
    )
    session.add_all([conda_operation, docker_operation])
    session.commit()

    claimed = HubRepository(session).claim_operation("docker")

    assert claimed is not None and claimed.operation_key == docker_operation.operation_key
    assert claimed.status == "PREPARING"
    assert conda_operation.status == "PENDING"


def test_transition_job_enforces_state_machine_and_records_terminal_metadata(
    session: Session,
) -> None:
    """Skipping state validation permits impossible lifecycle histories."""
    job = _job(_build("docker"), status="RUNNING")
    session.add(job)
    session.commit()

    transitioned = HubRepository(session).transition_job(
        job,
        JobStatus.TIMED_OUT,
        error_summary="execution exceeded its configured timeout",
        exit_code=124,
    )

    assert transitioned.status == "TIMED_OUT"
    assert transitioned.error_summary == "execution exceeded its configured timeout"
    assert transitioned.exit_code == 124
    assert transitioned.finished_at is not None
    with pytest.raises(ValueError, match="terminal"):
        HubRepository(session).transition_job(job, JobStatus.RUNNING)


def test_job_files_link_immutable_file_snapshot_to_job(session: Session) -> None:
    """Omitting the association snapshot makes historical job inputs irreproducible."""
    from hub_server.models import FileRecord

    job = _job(_build("docker"))
    file_record = FileRecord(
        file_key="file_input",
        scope="UPLOAD",
        role="INPUT",
        logical_name="source.nc",
        original_filename="source.nc",
        relative_path="uploads/file_input/payload",
        extension=".nc",
        size_bytes=3,
        sha256="f" * 64,
        status="AVAILABLE",
    )
    association = JobFile(
        job=job,
        file_record=file_record,
        role="INPUT",
        logical_name="source_nc",
        file_snapshot_json={"sha256": "f" * 64, "size": 3},
    )
    session.add(association)
    session.commit()

    assert association.job.job_key.startswith("job_")
    assert association.file_record.file_key == "file_input"
    assert association.file_snapshot_json == {"sha256": "f" * 64, "size": 3}


def test_generated_public_identifiers_are_unique(session: Session) -> None:
    """Replacing UUID-backed defaults with predictable or shared IDs breaks public identity."""
    build = _build("docker")
    jobs = [_job(build), _job(build)]
    operations = [
        RunnerOperation(
            plugin_build=build,
            runtime_type="docker",
            kind="INSTALL",
            status="PENDING",
            payload_json={},
        )
        for _ in range(2)
    ]
    session.add_all([*jobs, *operations])
    session.commit()

    assert len({job.job_key for job in jobs}) == 2
    assert all(job.job_key.startswith("job_") for job in jobs)
    assert len({operation.operation_key for operation in operations}) == 2
    assert all(operation.operation_key.startswith("operation_") for operation in operations)
