"""Job webhook callback inspection and replay endpoints (viewer/operator-gated)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import Job, JobCallback
from hub_server.services.audit import record as audit
from hub_server.services.webhooks import replay_callback

router = APIRouter(
    prefix="/jobs", tags=["webhooks"], dependencies=[Depends(require_role("viewer"))]
)


@router.get("/{job_key}/callbacks")
def list_job_callbacks(
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """List every webhook callback registered for one Job."""
    job = session.query(Job).filter(Job.job_key == job_key).one_or_none()
    if job is None:
        raise HubError(code="JOB_NOT_FOUND", message="任务不存在", status_code=404)
    callbacks = (
        session.query(JobCallback)
        .filter(JobCallback.job_id == job.id)
        .order_by(JobCallback.id)
        .all()
    )
    return {"items": [_callback_response(callback) for callback in callbacks]}


@router.post(
    "/{job_key}/callbacks/{callback_id}/replay",
    dependencies=[Depends(require_role("operator"))],
)
def replay_job_callback(
    job_key: str,
    callback_id: int,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Reset one FAILED or EXHAUSTED callback so the scheduler redelivers it."""
    job = session.query(Job).filter(Job.job_key == job_key).one_or_none()
    if job is None:
        raise HubError(code="JOB_NOT_FOUND", message="任务不存在", status_code=404)
    registered = session.get(JobCallback, callback_id)
    if registered is None or registered.job_id != job.id:
        raise HubError(code="CALLBACK_NOT_FOUND", message="回调不存在", status_code=404)
    callback = replay_callback(session, callback_id)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="webhook.replay",
        resource_type="job_callback",
        resource_id=str(callback_id),
        detail={"url": callback.url, "job_id": job.job_key},
        ip=actor_ip(http_request),
    )
    session.commit()
    return {
        "id": callback.id,
        "url": callback.url,
        "state": callback.state,
        "attempts": callback.attempts,
    }


def _callback_response(callback: JobCallback) -> dict[str, object]:
    """Serialize one callback without ever exposing its signing secret."""
    return {
        "id": callback.id,
        "url": callback.url,
        "state": callback.state,
        "attempts": callback.attempts,
        "last_status_code": callback.last_status_code,
        "last_error": callback.last_error,
        "next_attempt_at": _utc_iso(callback.next_attempt_at),
    }


def _utc_iso(value: datetime | None) -> str | None:
    """Normalize SQLite's timezone-naive values to an explicit UTC timestamp."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
