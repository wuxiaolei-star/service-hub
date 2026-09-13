"""Scheduler-side evaluation of due Schedules and Job creation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from python_hub_contracts import RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import Schedule
from hub_server.services.audit import record as audit
from hub_server.services.jobs import JobService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage


def trigger_due(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    now: datetime | None = None,
) -> int:
    """Create one Job per enabled Schedule whose next_run_at has passed.

    Each successful trigger records last_job_id, advances next_run_at by the
    configured interval, and writes one "schedule.trigger" audit entry attributed
    to the system scheduler. A schedule whose Job creation fails is audited with
    result="denied" (including the stable error code) and keeps its previous
    next_run_at so the scheduler retries it on the next pass. Returns the number
    of successful triggers.
    """
    current = _utc_now(now)
    schedules = session.scalars(
        select(Schedule)
        .where(Schedule.enabled.is_(True), Schedule.next_run_at <= current)
        .order_by(Schedule.id)
    ).all()
    triggered = 0
    for schedule in schedules:
        try:
            job = JobService(session, storage, settings).create(
                plugin_id=schedule.plugin_id,
                version=schedule.version,
                runtime_type=cast(RuntimeType, schedule.runtime_type),
                inputs=dict(schedule.inputs_json or {}),
                params=dict(schedule.params_json or {}),
            )
        except HubError as error:
            session.rollback()
            audit(
                session,
                actor_type="system",
                actor_id=None,
                actor_name="scheduler",
                action="schedule.trigger",
                resource_type="schedule",
                resource_id=str(schedule.id),
                detail={
                    "name": schedule.name,
                    "code": error.code,
                    "message": error.message,
                },
                result="denied",
            )
            session.commit()
            continue
        schedule.last_job_id = job.job_key
        schedule.next_run_at = schedule.next_run_at + timedelta(
            minutes=schedule.interval_minutes
        )
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="scheduler",
            action="schedule.trigger",
            resource_type="schedule",
            resource_id=job.job_key,
            detail={"schedule_id": schedule.id, "schedule_name": schedule.name},
        )
        session.commit()
        triggered += 1
    return triggered


def _utc_now(now: datetime | None) -> datetime:
    """Interpret a missing or naive evaluation time as the current UTC time."""
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)
