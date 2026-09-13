"""Scheduled job creation endpoints (operator-gated, admin-only delete)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import Schedule
from hub_server.services.audit import record as audit

router = APIRouter(
    prefix="/schedules", tags=["schedules"], dependencies=[Depends(require_role("operator"))]
)


class ScheduleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    plugin_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    runtime_type: str = Field(pattern="^(conda-pack|docker)$")
    inputs: dict[str, object] = Field(default_factory=dict)
    params: dict[str, object] = Field(default_factory=dict)
    interval_minutes: int = Field(ge=1)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_schedule(
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    request: ScheduleCreateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Register one periodic job creation rule."""
    existing = (
        session.query(Schedule).filter(Schedule.name == request.name).one_or_none()
    )
    if existing is not None:
        raise HubError(
            code="SCHEDULE_NAME_TAKEN",
            message="调度名称已存在",
            status_code=status.HTTP_409_CONFLICT,
        )
    schedule = Schedule(
        name=request.name,
        plugin_id=request.plugin_id,
        version=request.version,
        runtime_type=request.runtime_type,
        inputs_json=dict(request.inputs),
        params_json=dict(request.params),
        interval_minutes=request.interval_minutes,
        enabled=True,
        next_run_at=datetime.now(UTC) + timedelta(minutes=request.interval_minutes),
    )
    session.add(schedule)
    session.flush()
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="schedule.create",
        resource_type="schedule",
        resource_id=str(schedule.id),
        detail={
            "name": schedule.name,
            "plugin_id": schedule.plugin_id,
            "version": schedule.version,
            "runtime_type": schedule.runtime_type,
            "interval_minutes": schedule.interval_minutes,
        },
        ip=actor_ip(http_request),
    )
    session.commit()
    return _schedule_response(schedule)


@router.get("")
def list_schedules(session: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    """Return every registered schedule ordered by id."""
    schedules = session.query(Schedule).order_by(Schedule.id).all()
    return {"items": [_schedule_response(schedule) for schedule in schedules]}


@router.post("/{schedule_id}/enable")
def enable_schedule(
    schedule_id: int,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Enable one schedule so the scheduler may trigger it again."""
    schedule = _get_schedule(session, schedule_id)
    schedule.enabled = True
    _audit_schedule_change(
        session, actor, http_request, action="schedule.enable", schedule=schedule
    )
    session.commit()
    return _schedule_response(schedule)


@router.post("/{schedule_id}/disable")
def disable_schedule(
    schedule_id: int,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Disable one schedule so the scheduler skips it."""
    schedule = _get_schedule(session, schedule_id)
    schedule.enabled = False
    _audit_schedule_change(
        session, actor, http_request, action="schedule.disable", schedule=schedule
    )
    session.commit()
    return _schedule_response(schedule)


@router.delete(
    "/{schedule_id}", dependencies=[Depends(require_role("admin"))]
)
def delete_schedule(
    schedule_id: int,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Permanently remove one schedule (admin only)."""
    schedule = _get_schedule(session, schedule_id)
    detail: dict[str, object] = {"name": schedule.name}
    session.delete(schedule)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="schedule.delete",
        resource_type="schedule",
        resource_id=str(schedule_id),
        detail=detail,
        ip=actor_ip(http_request),
    )
    session.commit()
    return {"id": schedule_id, "deleted": True}


def _get_schedule(session: Session, schedule_id: int) -> Schedule:
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise HubError(code="SCHEDULE_NOT_FOUND", message="调度不存在", status_code=404)
    return schedule


def _audit_schedule_change(
    session: Session,
    actor: Actor,
    http_request: Request,
    *,
    action: str,
    schedule: Schedule,
) -> None:
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action=action,
        resource_type="schedule",
        resource_id=str(schedule.id),
        detail={"name": schedule.name, "enabled": schedule.enabled},
        ip=actor_ip(http_request),
    )


def _schedule_response(schedule: Schedule) -> dict[str, object]:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "plugin_id": schedule.plugin_id,
        "version": schedule.version,
        "runtime_type": schedule.runtime_type,
        "inputs": dict(schedule.inputs_json or {}),
        "params": dict(schedule.params_json or {}),
        "interval_minutes": schedule.interval_minutes,
        "enabled": schedule.enabled,
        "next_run_at": _utc_iso(schedule.next_run_at),
        "last_job_id": schedule.last_job_id,
        "created_at": _utc_iso(schedule.created_at),
        "updated_at": _utc_iso(schedule.updated_at),
    }


def _utc_iso(value: datetime) -> str:
    """Normalize SQLite's timezone-naive values to an explicit UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
