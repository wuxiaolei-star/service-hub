"""Public Job lifecycle endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, status
from python_hub_contracts import JobStatus, RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.models import FileRecord, Job
from hub_server.routers.files import _file_response
from hub_server.schemas import (
    FileResponse,
    JobCancelResponse,
    JobCreateRequest,
    JobListResponse,
    JobLogResponse,
    JobOutputsResponse,
    JobResponse,
)
from hub_server.services.audit import record as audit
from hub_server.services.jobs import JobService
from hub_server.services.quotas import QuotaService
from hub_server.services.webhooks import enqueue_callback
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(
    prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_role("viewer"))]
)


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def create_job(
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    request: JobCreateRequest,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> JobResponse:
    """Create a pending Job for one exact enabled runtime Build."""
    QuotaService(session, settings.quotas).enforce_job_creation(actor)
    job = JobService(session, storage, settings).create(
        plugin_id=request.plugin_id,
        version=request.version,
        runtime_type=request.runtime_type,
        inputs=request.inputs,
        params=request.params,
        owner_user_id=actor.id if actor.kind == "user" else None,
    )
    if request.callback is not None:
        enqueue_callback(
            session,
            job.id,
            request.callback.url,
            request.callback.secret,
        )
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="job.create",
        resource_type="job",
        resource_id=job.job_key,
        ip=actor_ip(http_request),
    )
    session.commit()
    return _job_response(job)


@router.get("", response_model=JobListResponse)
def list_jobs(
    session: Annotated[Session, Depends(get_session)],
) -> JobListResponse:
    """Return recent Jobs."""
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()
    return JobListResponse(items=[_job_response(job) for job in jobs])


@router.get("/{job_key}", response_model=JobResponse)
def get_job(
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> JobResponse:
    """Return one Job summary."""
    job = session.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        from hub_server.errors import HubError

        raise HubError(code="JOB_NOT_FOUND", message="Job 不存在", status_code=404)
    return _job_response(job)


@router.post(
    "/{job_key}/cancel",
    response_model=JobCancelResponse,
    status_code=202,
    dependencies=[Depends(require_role("operator"))],
)
def cancel_job(
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> JobCancelResponse:
    """Request idempotent cancellation; the runner owns terminal completion."""
    job = JobService(session, storage, settings).cancel(job_key)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="job.cancel",
        resource_type="job",
        resource_id=job.job_key,
        ip=actor_ip(http_request),
    )
    return JobCancelResponse(
        job_id=job.job_key,
        status=JobStatus(job.status),
        cancel_requested=job.cancel_requested,
    )


@router.get("/{job_key}/logs", response_model=JobLogResponse)
def get_job_logs(
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
    cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JobLogResponse:
    """Return cursor-based runner events for a Job."""
    items, next_cursor = JobService(session, storage, settings).logs(
        job_key,
        cursor=cursor,
        limit=limit,
    )
    return JobLogResponse(items=items, next_cursor=next_cursor)


@router.get("/{job_key}/outputs", response_model=JobOutputsResponse)
def get_job_outputs(
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> JobOutputsResponse:
    """Return output file summaries only after successful completion."""
    records = JobService(session, storage, settings).outputs(job_key)
    return JobOutputsResponse(items=[_file_summary(record) for record in records])


def _job_response(job: Job) -> JobResponse:
    plugin_version = job.plugin_build.plugin_version
    return JobResponse(
        job_id=job.job_key,
        plugin_id=plugin_version.plugin.plugin_key,
        version=plugin_version.version,
        build_id=job.plugin_build.build_key,
        runtime_type=cast(RuntimeType, job.runtime_type),
        status=JobStatus(job.status),
        cancel_requested=job.cancel_requested,
        error_summary=job.error_summary,
        created_at=_utc_timestamp(job.created_at),
        started_at=_utc_timestamp(job.started_at) if job.started_at is not None else None,
        finished_at=_utc_timestamp(job.finished_at) if job.finished_at is not None else None,
    )


def _utc_timestamp(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive values for the public UTC contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _file_summary(record: FileRecord) -> FileResponse:
    return _file_response(record)
