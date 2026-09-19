"""Scheduler-side evaluation of due Schedules and Job creation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from croniter import croniter  # type: ignore[import-untyped]
from python_hub_contracts import RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import Schedule
from hub_server.services.audit import record as audit
from hub_server.services.jobs import JobService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

MissedRunPolicy = Literal["skip", "catch_up", "latest"]

# Upper bound on how many missed cron fires one scheduler pass will replay,
# so a long outage cannot explode into an unbounded job backlog.
_CATCH_UP_LIMIT = 10


def trigger_due(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    now: datetime | None = None,
) -> int:
    """Create Jobs for enabled Schedules whose next_run_at has passed.

    Interval schedules create one Job and advance next_run_at by the configured
    interval. Cron schedules (cron_expr set) compute the next fire with croniter
    and honor missed_run_policy: "skip" (default) collapses every missed fire
    into a single run, "catch_up" replays each missed fire individually capped
    at 10 runs per pass, and "latest" runs only the latest missed fire.

    Each successful trigger records last_job_id and writes one "schedule.trigger"
    audit entry attributed to the system scheduler. A schedule whose Job creation
    fails is audited with result="denied" (including the stable error code) and
    keeps its pending fire time so the scheduler retries it on the next pass.
    Returns the number of successful triggers.
    """
    current = _utc_now(now)
    schedules = session.scalars(
        select(Schedule)
        .where(Schedule.enabled.is_(True), Schedule.next_run_at <= current)
        .order_by(Schedule.id)
    ).all()
    triggered = 0
    for schedule in schedules:
        if schedule.cron_expr is not None:
            triggered += _trigger_cron_schedule(session, storage, settings, schedule, current)
        else:
            triggered += _trigger_interval_schedule(session, storage, settings, schedule)
    return triggered


def _trigger_interval_schedule(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    schedule: Schedule,
) -> int:
    """Create one Job for an interval schedule and advance by interval_minutes."""
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
        return 0
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
    return 1


def _trigger_cron_schedule(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    schedule: Schedule,
    current: datetime,
) -> int:
    """Create Jobs for a cron schedule according to its missed_run_policy."""
    cron_expr = schedule.cron_expr
    if cron_expr is None:  # defensive; callers select cron schedules only
        return 0
    policy = schedule.missed_run_policy or "skip"
    pending = _as_utc(schedule.next_run_at)
    if policy == "catch_up":
        occurrences, following = _catch_up_plan(cron_expr, pending, current)
    else:
        # "skip" and "latest" both run once; only the naming of the intent
        # differs (drop the backlog vs. run the most recent missed fire).
        occurrences = [pending]
        following = cast(datetime, croniter(cron_expr, current).get_next(datetime))
    created = 0
    for occurrence in occurrences:
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
                    "planned_for": occurrence.isoformat(),
                },
                result="denied",
            )
            # Retry the failed occurrence on the next scheduler pass.
            schedule.next_run_at = occurrence
            session.commit()
            return created
        schedule.last_job_id = job.job_key
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="scheduler",
            action="schedule.trigger",
            resource_type="schedule",
            resource_id=job.job_key,
            detail={
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "planned_for": occurrence.isoformat(),
            },
        )
        session.commit()
        created += 1
    schedule.next_run_at = following
    session.commit()
    return created


def _catch_up_plan(
    cron_expr: str,
    pending: datetime,
    current: datetime,
) -> tuple[list[datetime], datetime]:
    """Enumerate missed cron fires and the first fire that follows them.

    ``pending`` itself is the currently due fire; subsequent fires up to and
    including ``current`` are also missed. The backlog is capped at 10 fires;
    when the cap binds, the returned following fire is still in the past so the
    next pass continues catching up.
    """
    occurrences = [pending]
    iterator = croniter(cron_expr, pending)
    following = cast(datetime, iterator.get_next(datetime))
    while len(occurrences) < _CATCH_UP_LIMIT and following <= current:
        occurrences.append(following)
        following = cast(datetime, iterator.get_next(datetime))
    return occurrences, following


def _utc_now(now: datetime | None) -> datetime:
    """Interpret a missing or naive evaluation time as the current UTC time."""
    if now is None:
        return datetime.now(UTC)
    return _as_utc(now)


def _as_utc(value: datetime) -> datetime:
    """Normalize a naive (SQLite) datetime to an explicit UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
