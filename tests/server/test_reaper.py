"""Zombie Job reaper rules for Jobs abandoned by lost runners."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hub_server.models import AuditLogRecord, Job, Plugin, PluginBuild, PluginVersion
from hub_server.services.reaper import reap_stale_jobs
from sqlalchemy.orm import Session


def _add_build(session: Session) -> PluginBuild:
    build = PluginBuild(
        plugin_version=PluginVersion(
            plugin=Plugin(plugin_key=f"plugin_{uuid4().hex[:12]}", name="Reaper Plugin"),
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"plugin": {"id": "reaper", "version": "1.0.0"}},
            status="INSTALLED",
        ),
        manifest_build_id="package-build-reaper",
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
    status: str,
    age_seconds: float,
    timeout_seconds: int = 60,
) -> Job:
    """Create an active-state Job whose last update happened ``age_seconds`` ago."""
    job = Job(
        plugin_build=build,
        runtime_type=build.runtime_type,
        runtime_fingerprint=build.runtime_fingerprint,
        status=status,
        params_json={},
        inputs_json={},
        timeout_seconds=timeout_seconds,
        updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )
    session.add(job)
    session.commit()
    return job


def test_stale_preparing_job_is_timed_out_and_audited(session: Session) -> None:
    build = _add_build(session)
    now = datetime.now(UTC)
    # Deadline: timeout 60s + 600s grace = 660s; the claim was 700s ago.
    job = _add_job(session, build, status="PREPARING", age_seconds=700, timeout_seconds=60)
    job_key = job.job_key

    reaped = reap_stale_jobs(session, now=now)

    assert reaped == 1
    assert job.status == "TIMED_OUT"
    assert job.error_summary == "Runner 失联，任务被 reaper 回收"  # noqa: RUF001
    assert job.finished_at is not None

    entry = session.query(AuditLogRecord).filter_by(action="job.reap").one()
    assert entry.actor_type == "system"
    assert entry.actor_name == "scheduler"
    assert entry.resource_type == "job"
    assert entry.resource_id == job_key
    assert entry.result == "ok"


def test_running_job_within_timeout_is_untouched(session: Session) -> None:
    build = _add_build(session)
    now = datetime.now(UTC)
    job = _add_job(
        session, build, status="RUNNING", age_seconds=1200, timeout_seconds=3600
    )
    job_key = job.job_key

    reaped = reap_stale_jobs(session, now=now)

    assert reaped == 0
    assert job.status == "RUNNING"
    assert session.query(AuditLogRecord).filter_by(action="job.reap").count() == 0
    assert session.query(Job).filter_by(job_key=job_key).one().status == "RUNNING"


def test_terminal_jobs_are_never_reaped(session: Session) -> None:
    build = _add_build(session)
    now = datetime.now(UTC)
    job = _add_job(
        session, build, status="SUCCESS", age_seconds=10 * 86400, timeout_seconds=60
    )
    job_key = job.job_key

    reaped = reap_stale_jobs(session, now=now)

    assert reaped == 0
    assert session.query(Job).filter_by(job_key=job_key).one().status == "SUCCESS"


def test_timeout_seconds_participates_in_the_deadline(session: Session) -> None:
    build = _add_build(session)
    now = datetime.now(UTC)
    # Same claim age: a 60s-timeout Job is past its 660s deadline while a
    # 3600s-timeout Job still has hours of runway left.
    short = _add_job(session, build, status="RUNNING", age_seconds=700, timeout_seconds=60)
    long_running = _add_job(
        session, build, status="RUNNING", age_seconds=700, timeout_seconds=3600
    )
    short_key, long_key = short.job_key, long_running.job_key

    reaped = reap_stale_jobs(session, now=now)

    assert reaped == 1
    assert session.query(Job).filter_by(job_key=short_key).one().status == "TIMED_OUT"
    assert session.query(Job).filter_by(job_key=long_key).one().status == "RUNNING"


def test_preparing_job_within_grace_window_is_untouched(session: Session) -> None:
    build = _add_build(session)
    now = datetime.now(UTC)
    job = _add_job(session, build, status="PREPARING", age_seconds=100, timeout_seconds=60)
    job_key = job.job_key

    reaped = reap_stale_jobs(session, now=now)

    assert reaped == 0
    assert session.query(Job).filter_by(job_key=job_key).one().status == "PREPARING"
