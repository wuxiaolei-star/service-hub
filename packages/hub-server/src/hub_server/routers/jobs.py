"""Public Job lifecycle endpoints."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from python_hub_contracts import JobStatus, RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.models import FileRecord, Job, PluginBuild, PluginVersion
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
from hub_server.services.webhook_guard import normalize_webhook_url
from hub_server.services.webhooks import enqueue_callback
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(
    prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_role("viewer"))]
)

_JOBS_CSV_HEADER: tuple[str, ...] = (
    "job_id",
    "plugin_id",
    "version",
    "runtime_type",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "error_summary",
)
_JOBS_EXPORT_LIMIT = 10000
_JOBS_EXPORT_BATCH_SIZE = 500
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@")


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
    if request.callback is not None:
        # JobService.create commits on its own, so a callback rejected after it would
        # leave an orphan PENDING Job queued for a runner. Validate the target first;
        # enqueue_callback re-validates when it persists the row.
        normalize_webhook_url(request.callback.url, settings.webhooks)
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
            policy=settings.webhooks,
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
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
    status_filter: Annotated[list[str] | None, Query(alias="status")] = None,
    plugin_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    """Return filtered Jobs newest-first with the total matching the filters."""
    jobs, total = JobService(session, storage, settings).list_jobs(
        status=status_filter,
        plugin_id=plugin_id,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(items=[_job_response(job) for job in jobs], total=total)


# Registered before the "/{job_key}" routes so "export" is never read as a key.
@router.get(
    "/export",
    response_class=StreamingResponse,
    dependencies=[Depends(require_role("operator"))],
)
def export_jobs(
    session: Annotated[Session, Depends(get_session)],
) -> StreamingResponse:
    """Stream the 10000 newest Jobs as a CSV attachment for spreadsheet consumers."""
    return StreamingResponse(
        _jobs_csv_rows(session),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="jobs.csv"'},
    )


def _jobs_csv_rows(session: Session) -> Iterator[str]:
    """Stream the header line plus the newest Jobs newest-first, in batches."""
    yield _csv_line(_JOBS_CSV_HEADER)
    jobs = list(
        session.scalars(
            select(Job)
            .options(
                joinedload(Job.plugin_build)
                .joinedload(PluginBuild.plugin_version)
                .joinedload(PluginVersion.plugin)
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(_JOBS_EXPORT_LIMIT)
        )
    )
    for start in range(0, len(jobs), _JOBS_EXPORT_BATCH_SIZE):
        for job in jobs[start : start + _JOBS_EXPORT_BATCH_SIZE]:
            plugin_version = job.plugin_build.plugin_version
            yield _csv_line(
                (
                    job.job_key,
                    plugin_version.plugin.plugin_key,
                    plugin_version.version,
                    job.runtime_type,
                    job.status,
                    _csv_timestamp(job.created_at),
                    _csv_timestamp(job.started_at),
                    _csv_timestamp(job.finished_at),
                    job.error_summary,
                )
            )


def _csv_timestamp(value: datetime | None) -> str:
    """Render a lifecycle timestamp for CSV, empty when the Job never reached it."""
    if value is None:
        return ""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _csv_line(values: Sequence[object]) -> str:
    """Render one CSV row with formula-injection neutralisation applied per cell."""
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerow(_guarded_cell(value) for value in values)
    return buffer.getvalue()


def _guarded_cell(value: object) -> str:
    """Prefix cells that a spreadsheet could interpret as a formula."""
    text = "" if value is None else str(value)
    if text.startswith(_CSV_INJECTION_PREFIXES):
        return f"'{text}"
    return text


@router.post(
    "/{job_key}/rerun",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def rerun_job(
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> JobResponse:
    """Replay one terminal Job's snapshot as a fresh pending Job."""
    service = JobService(session, storage, settings)
    original = service.terminal_job(job_key)
    plugin_version = original.plugin_build.plugin_version
    job = service.create(
        plugin_id=plugin_version.plugin.plugin_key,
        version=plugin_version.version,
        runtime_type=cast(RuntimeType, original.runtime_type),
        inputs=original.inputs_json,
        params=original.params_json,
        owner_user_id=actor.id if actor.kind == "user" else None,
        replayed_from=original.job_key,
    )
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="job.rerun",
        resource_type="job",
        resource_id=job.job_key,
        detail={"replayed_from": original.job_key},
        ip=actor_ip(http_request),
    )
    session.commit()
    return _job_response(job)


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
        replayed_from=job.replayed_from,
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
