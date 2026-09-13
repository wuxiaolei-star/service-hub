"""Pipeline run advancement service tests ($prev wiring and terminal states)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from hub_server.dependencies_auth import Actor
from hub_server.main import create_app
from hub_server.models import (
    AuditLogRecord,
    Environment,
    FileRecord,
    Job,
    JobFile,
    Pipeline,
    Plugin,
    PluginBuild,
    PluginVersion,
)
from hub_server.services.pipelines import advance_runs, start_run
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from hub_server.storage import LocalStorage
from sqlalchemy.orm import Session

_ACTOR = Actor(kind="user", id=None, name="admin", role="admin")


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_settings(tmp_path)))


def _admin_headers(client: TestClient) -> dict[str, str]:
    from hub_server.models import UserRecord
    from hub_server.services.auth import AuthService

    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _step(inputs: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "plugin_id": "nc_to_shp",
        "version": "1.0.0",
        "runtime_type": "docker",
        "inputs": inputs if inputs is not None else {},
        "params": {},
    }


def _make_pipeline(session: Session, steps: list[dict[str, object]], name: str) -> Pipeline:
    pipeline = Pipeline(name=name, steps_json=steps)
    session.add(pipeline)
    session.commit()
    return pipeline


def test_advance_single_step_success_completes_run(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        settings = client.app.state.settings
        storage: LocalStorage = client.app.state.storage
        with client.app.state.session_factory() as session:
            _seed_build(session)
            file_id = _upload_nc(client, _admin_headers(client))
            pipeline = _make_pipeline(session, [_step({"source_nc": file_id})], "single")
            run, job = start_run(session, storage, settings, pipeline, _ACTOR)
            assert run.state == "RUNNING"
            assert run.current_step == 0

            job.status = "SUCCESS"
            session.commit()

            processed = advance_runs(session, storage, settings)

            assert processed == 1
            session.refresh(run)
            assert run.state == "SUCCEEDED"
            assert run.current_step == 0


def test_advance_two_steps_creates_next_job_with_resolved_input(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        settings = client.app.state.settings
        storage: LocalStorage = client.app.state.storage
        with client.app.state.session_factory() as session:
            _seed_build(session)
            file_id = _upload_nc(client, _admin_headers(client))
            pipeline = _make_pipeline(
                session,
                [_step({"source_nc": file_id}), _step({"source_nc": "$prev.result"})],
                "chained",
            )
            run, first_job = start_run(session, storage, settings, pipeline, _ACTOR)
            first_job.status = "SUCCESS"
            session.commit()
            output_record = _register_output(session, storage, first_job, "result")

            processed = advance_runs(session, storage, settings)

            assert processed == 1
            session.refresh(run)
            assert run.state == "RUNNING"
            assert run.current_step == 1
            second_jobs = (
                session.query(Job)
                .filter(Job.pipeline_run_id == run.id, Job.id != first_job.id)
                .all()
            )
            assert len(second_jobs) == 1
            second_job = second_jobs[0]
            assert second_job.status == "PENDING"
            assert second_job.inputs_json == {"source_nc": output_record.file_key}
            steps_audit = (
                session.query(AuditLogRecord)
                .filter(AuditLogRecord.action == "pipeline.step")
                .order_by(AuditLogRecord.id)
                .all()
            )
            assert [entry.detail["step"] for entry in steps_audit] == [0, 1]
            assert steps_audit[1].detail["run_id"] == run.id

            # Completing the final step terminates the run.
            second_job.status = "SUCCESS"
            session.commit()
            assert advance_runs(session, storage, settings) == 1
            session.refresh(run)
            assert run.state == "SUCCEEDED"


def test_advance_skips_non_terminal_jobs(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        settings = client.app.state.settings
        storage: LocalStorage = client.app.state.storage
        with client.app.state.session_factory() as session:
            _seed_build(session)
            file_id = _upload_nc(client, _admin_headers(client))
            pipeline = _make_pipeline(session, [_step({"source_nc": file_id})], "pending")

            run, _job = start_run(session, storage, settings, pipeline, _ACTOR)

            processed = advance_runs(session, storage, settings)

            assert processed == 0
            session.refresh(run)
            assert run.state == "RUNNING"
            assert run.current_step == 0


def test_advance_failed_first_step_fails_run(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        settings = client.app.state.settings
        storage: LocalStorage = client.app.state.storage
        with client.app.state.session_factory() as session:
            _seed_build(session)
            file_id = _upload_nc(client, _admin_headers(client))
            pipeline = _make_pipeline(session, [_step({"source_nc": file_id}), _step()], "failing")
            run, first_job = start_run(session, storage, settings, pipeline, _ACTOR)
            first_job.status = "FAILED"
            session.commit()

            processed = advance_runs(session, storage, settings)

            assert processed == 1
            session.refresh(run)
            assert run.state == "FAILED"
            assert run.current_step == 0
            denied = session.query(AuditLogRecord).filter(AuditLogRecord.result == "denied").all()
            assert len(denied) == 1
            assert denied[0].detail["run_id"] == run.id


def test_advance_missing_prev_output_fails_run(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        settings = client.app.state.settings
        storage: LocalStorage = client.app.state.storage
        with client.app.state.session_factory() as session:
            _seed_build(session)
            file_id = _upload_nc(client, _admin_headers(client))
            pipeline = _make_pipeline(
                session,
                [_step({"source_nc": file_id}), _step({"source_nc": "$prev.missing"})],
                "broken-ref",
            )
            run, first_job = start_run(session, storage, settings, pipeline, _ACTOR)
            first_job.status = "SUCCESS"
            session.commit()
            _register_output(session, storage, first_job, "result")

            processed = advance_runs(session, storage, settings)

            assert processed == 1
            session.refresh(run)
            assert run.state == "FAILED"
            job_count = session.query(Job).filter(Job.pipeline_run_id == run.id).count()
            assert job_count == 1
            denied = session.query(AuditLogRecord).filter(AuditLogRecord.result == "denied").all()
            assert len(denied) == 1


def _upload_nc(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["file_id"])


def _register_output(
    session: Session, storage: LocalStorage, job: Job, logical_name: str
) -> FileRecord:
    """Register one plausible Job output file with matching on-disk payload."""
    payload = f"output-of-{job.job_key}".encode()
    relative_path = f"jobs/{job.job_key}/output/{logical_name}.nc"
    output = storage.open_relative(relative_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    record = FileRecord(
        file_key=f"file_{uuid4().hex}",
        scope="JOB",
        role="OUTPUT",
        logical_name=logical_name,
        original_filename=f"{logical_name}.nc",
        relative_path=relative_path,
        extension=".nc",
        mime_type=None,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        status="AVAILABLE",
    )
    association = JobFile(
        job_id=job.id,
        file_record=record,
        role="OUTPUT",
        logical_name=logical_name,
        file_snapshot_json={
            "file_id": f"file_{uuid4().hex[:8]}",
            "name": f"{logical_name}.nc",
            "path": f"output/{logical_name}.nc",
            "size": len(payload),
            "extension": ".nc",
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    )
    session.add_all([record, association])
    session.commit()
    return record


def _seed_build(session: Session, *, runtime_type: str = "docker") -> None:
    """Seed one enabled nc_to_shp docker build with its ready environment."""
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
    runtime_metadata = {
        "type": "docker",
        "archive": "image.tar.zst",
        "image": "nc",
        "digest": "d" * 64,
    }
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
        runtime_fingerprint=str(runtime_metadata["digest"]),
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
        fingerprint=str(runtime_metadata["digest"]),
        environment_path=None,
        image_digest=str(runtime_metadata["digest"]),
        metadata_json=runtime_metadata,
        status="READY",
    )
    session.add_all([plugin, version, build, environment])
    session.commit()


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
