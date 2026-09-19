"""Retention lifecycle rules for files, terminal Jobs, sessions, and audit logs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from hub_server.models import (
    AuditLogRecord,
    FileRecord,
    Job,
    JobCallback,
    JobFile,
    Plugin,
    PluginBuild,
    PluginVersion,
    SessionRecord,
    UserRecord,
)
from hub_server.services.retention import (
    RetentionSweeper,
    sweep_expired_sessions,
    sweep_old_audit_logs,
    sweep_terminal_jobs,
)
from hub_server.settings import RetentionSettings
from sqlalchemy.orm import Session


def _add_file(
    session: Session,
    *,
    file_key: str,
    role: str = "INPUT",
    age_hours: float,
) -> FileRecord:
    created_at = datetime.now(UTC) - timedelta(hours=age_hours)
    record = FileRecord(
        file_key=file_key,
        logical_name=f"{file_key}.nc",
        original_filename=f"{file_key}.nc",
        relative_path=f"files/{file_key}.nc",
        size_bytes=10,
        sha256="a" * 64,
        role=role,
        created_at=created_at,
    )
    session.add(record)
    session.commit()
    return record


def _payload(storage_root, record: FileRecord) -> Path:
    payload = storage_root / record.relative_path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"payload")
    return payload


def _sweeper(session: Session, storage_root, ttl_hours: float = 720) -> RetentionSweeper:
    return RetentionSweeper(
        session, storage_root, RetentionSettings(input_ttl_hours=int(ttl_hours))
    )


def test_expired_unreferenced_input_is_deleted(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_old", age_hours=800)
    payload = _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 1
    assert session.get(FileRecord, record.id) is None
    assert not payload.exists()


def test_recent_input_is_kept(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_new", age_hours=1)
    _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 0
    assert session.get(FileRecord, record.id) is not None


def test_output_files_are_never_deleted(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_out", role="OUTPUT", age_hours=9999)
    _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 0
    assert session.get(FileRecord, record.id) is not None


def test_missing_payload_does_not_break_sweep(session: Session, tmp_path) -> None:
    _add_file(session, file_key="file_nofile", age_hours=9999)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 1


def test_sweep_deletion_is_audited(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_aud", age_hours=9999)
    _payload(tmp_path, record)

    _sweeper(session, tmp_path).sweep()

    from hub_server.models import AuditLogRecord

    entry = session.query(AuditLogRecord).filter_by(action="file.retention_delete").one()
    assert entry.resource_id == record.file_key
    assert entry.actor_name == "cleaner"


def _add_build(session: Session) -> PluginBuild:
    build = PluginBuild(
        plugin_version=PluginVersion(
            plugin=Plugin(plugin_key=f"plugin_{uuid4().hex[:12]}", name="Retention Plugin"),
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"plugin": {"id": "retention", "version": "1.0.0"}},
            status="INSTALLED",
        ),
        manifest_build_id="package-build-retention",
        target_os="linux",
        target_arch="amd64",
        runtime_type="docker",
        python_version="3.10",
        sdk_version="1.0.0",
        package_sha256="d" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/archive.tar.zst",
        runtime_fingerprint="d" * 64,
        build_metadata_json={"runtime": {"type": "docker"}},
        status="ENABLED",
    )
    session.add(build)
    session.commit()
    return build


def _add_job(
    session: Session,
    build: PluginBuild,
    *,
    status: str = "SUCCESS",
    age_days: float = 40,
    workspace: bool = False,
) -> Job:
    job = Job(
        plugin_build=build,
        runtime_type=build.runtime_type,
        runtime_fingerprint=build.runtime_fingerprint,
        status=status,
        params_json={},
        inputs_json={},
        timeout_seconds=60,
        updated_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    session.add(job)
    session.flush()
    if workspace:
        job.workspace_path = f"jobs/{job.job_key}"
        # Re-stamp the age: assigning workspace_path fires the onupdate hook.
        job.updated_at = datetime.now(UTC) - timedelta(days=age_days)
    session.commit()
    return job


def _add_job_file(session: Session, job: Job, *, role: str = "OUTPUT") -> FileRecord:
    file_key = f"file_{uuid4().hex[:12]}"
    relative_path = (
        f"jobs/{job.job_key}/output/{file_key}.nc"
        if role == "OUTPUT"
        else f"files/{file_key}.nc"
    )
    record = FileRecord(
        file_key=file_key,
        scope="JOB" if role == "OUTPUT" else "UPLOAD",
        role=role,
        logical_name=f"{file_key}.nc",
        original_filename=f"{file_key}.nc",
        relative_path=relative_path,
        size_bytes=10,
        sha256="b" * 64,
        status="AVAILABLE",
        created_at=datetime.now(UTC) - timedelta(days=40),
    )
    session.add_all(
        [
            record,
            JobFile(
                job=job,
                file_record=record,
                role=role,
                logical_name=record.logical_name,
                file_snapshot_json={"file_id": file_key},
            ),
        ]
    )
    session.commit()
    return record


def _write_payload(storage_root: Path, relative_path: str) -> Path:
    payload = storage_root / relative_path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"payload")
    return payload


def _make_workspace(storage_root: Path, workspace_path: str) -> Path:
    workspace = storage_root / workspace_path
    (workspace / "output").mkdir(parents=True)
    (workspace / "job.json").write_text("{}", encoding="utf-8")
    return workspace


def test_expired_terminal_job_is_deleted_with_workspace_and_outputs(
    session: Session, tmp_path
) -> None:
    build = _add_build(session)
    job = _add_job(session, build, status="SUCCESS", age_days=40, workspace=True)
    job_id, job_key, workspace_path = job.id, job.job_key, job.workspace_path
    output = _add_job_file(session, job, role="OUTPUT")
    output_id = output.id
    workspace = _make_workspace(tmp_path, workspace_path)
    payload = _write_payload(tmp_path, output.relative_path)
    input_record = _add_job_file(session, job, role="INPUT")
    input_id, input_payload_path = input_record.id, input_record.relative_path
    input_payload = _write_payload(tmp_path, input_payload_path)
    session.add(JobCallback(job_id=job_id, url="https://hooks.example.com/cb"))
    session.commit()

    deleted = sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    )

    assert deleted == 1
    assert session.get(Job, job_id) is None
    assert not workspace.exists()
    assert not payload.exists()
    assert session.get(FileRecord, output_id) is None
    assert session.query(JobFile).filter_by(job_id=job_id).count() == 0
    assert session.query(JobCallback).filter_by(job_id=job_id).count() == 0
    # INPUT snapshots belong to their uploading users; they age out separately.
    assert session.get(FileRecord, input_id) is not None
    assert input_payload.exists()

    entry = session.query(AuditLogRecord).filter_by(action="job.retention_delete").one()
    assert entry.actor_type == "system"
    assert entry.actor_name == "cleaner"
    assert entry.resource_type == "job"
    assert entry.resource_id == job_key
    assert entry.detail is not None and entry.detail["workspace"] == workspace_path


def test_recent_terminal_job_is_kept(session: Session, tmp_path) -> None:
    build = _add_build(session)
    job = _add_job(session, build, status="SUCCESS", age_days=1, workspace=True)
    job_id = job.id
    output = _add_job_file(session, job, role="OUTPUT")
    payload = _write_payload(tmp_path, output.relative_path)

    deleted = sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    )

    assert deleted == 0
    assert session.get(Job, job_id) is not None
    assert payload.exists()


def test_non_terminal_job_is_kept(session: Session, tmp_path) -> None:
    build = _add_build(session)
    job = _add_job(session, build, status="RUNNING", age_days=400)
    job_id = job.id

    deleted = sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    )

    assert deleted == 0
    assert session.get(Job, job_id) is not None


def test_missing_output_payload_does_not_break_job_sweep(session: Session, tmp_path) -> None:
    build = _add_build(session)
    job = _add_job(session, build, status="FAILED", age_days=40)
    _add_job_file(session, job, role="OUTPUT")

    deleted = sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    )

    assert deleted == 1


def test_terminal_job_sweep_respects_batch_limit(session: Session, tmp_path) -> None:
    build = _add_build(session)
    stale = datetime.now(UTC) - timedelta(days=40)
    session.add_all(
        Job(
            plugin_build=build,
            runtime_type=build.runtime_type,
            runtime_fingerprint=build.runtime_fingerprint,
            status="SUCCESS",
            params_json={},
            inputs_json={},
            timeout_seconds=60,
            updated_at=stale,
        )
        for _ in range(505)
    )
    session.commit()

    deleted = sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    )

    assert deleted == 500
    assert session.query(Job).filter_by(status="SUCCESS").count() == 5
    assert sweep_terminal_jobs(
        session, tmp_path, RetentionSettings(job_retention_days=30)
    ) == 5


def test_expired_sessions_are_swept(session: Session) -> None:
    user = UserRecord(username="sweep_user", password_hash="x" * 64)
    session.add(user)
    session.commit()
    expired = SessionRecord(
        user_id=user.id,
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    live = SessionRecord(user_id=user.id, token_hash="b" * 64)
    session.add_all([expired, live])
    session.commit()
    expired_id, live_id = expired.id, live.id

    deleted = sweep_expired_sessions(session)

    assert deleted == 1
    assert session.get(SessionRecord, expired_id) is None
    assert session.get(SessionRecord, live_id) is not None
    entry = session.query(AuditLogRecord).filter_by(action="session.sweep").one()
    assert entry.actor_name == "cleaner"
    assert entry.detail is not None and entry.detail["deleted"] == 1


def test_expired_audit_logs_are_swept(session: Session) -> None:
    old = AuditLogRecord(
        at=datetime.now(UTC) - timedelta(days=200),
        actor_type="system",
        actor_id=None,
        actor_name="old-service",
        action="job.create",
    )
    recent = AuditLogRecord(
        actor_type="system", actor_id=None, actor_name="young-service", action="job.create"
    )
    session.add_all([old, recent])
    session.commit()
    old_id, recent_id = old.id, recent.id

    deleted = sweep_old_audit_logs(session, RetentionSettings(audit_retention_days=180))

    assert deleted == 1
    assert session.get(AuditLogRecord, old_id) is None
    assert session.get(AuditLogRecord, recent_id) is not None
    summary = session.query(AuditLogRecord).filter_by(action="audit.sweep").one()
    assert summary.detail is not None and summary.detail["deleted"] == 1
