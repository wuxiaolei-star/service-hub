"""Reaper for Jobs abandoned by a lost runner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from python_hub_contracts import JobStatus
from sqlalchemy.orm import Session

from hub_server.models import Job
from hub_server.services.audit import record as audit

REAP_GRACE_SECONDS = 600

ACTIVE_JOB_STATUSES = (JobStatus.PREPARING.value, JobStatus.RUNNING.value)


def _as_aware(value: datetime) -> datetime:
    """SQLite round-trips timestamps as naive UTC; compare in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def reap_stale_jobs(session: Session, now: datetime | None = None) -> int:
    """Time out active Jobs whose runner has been silent past the deadline.

    A Job is stale when it sits in PREPARING or RUNNING and its ``updated_at``
    is older than ``timeout_seconds + REAP_GRACE_SECONDS``. Claiming and every
    lifecycle transition stamp ``updated_at`` (see ``HubRepository``), so a live
    runner keeps pushing the deadline forward without extra heartbeats. The
    grace window on top of the plugin timeout absorbs runner polling lag before
    the Job is declared TIMED_OUT. Returns the number of reaped Jobs.
    """
    current = now if now is not None else datetime.now(UTC)
    candidates = (
        session.query(Job)
        .filter(
            Job.status.in_(ACTIVE_JOB_STATUSES),
            # Loose pre-filter: no positive timeout can push the real deadline
            # before the grace window itself.
            Job.updated_at < current - timedelta(seconds=REAP_GRACE_SECONDS),
        )
        .all()
    )
    reaped = 0
    for job in candidates:
        stale_before = current - timedelta(
            seconds=REAP_GRACE_SECONDS + max(job.timeout_seconds, 0)
        )
        if _as_aware(job.updated_at) >= stale_before:
            continue
        job.status = JobStatus.TIMED_OUT.value
        job.error_summary = "Runner 失联，任务被 reaper 回收"  # noqa: RUF001
        job.finished_at = current
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="scheduler",
            action="job.reap",
            resource_type="job",
            resource_id=job.job_key,
            detail={"timeout_seconds": job.timeout_seconds},
        )
        reaped += 1
    if reaped:
        session.commit()
    return reaped
