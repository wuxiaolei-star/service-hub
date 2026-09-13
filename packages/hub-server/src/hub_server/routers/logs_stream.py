"""Server-Sent Events streaming for one Job's runner log."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import require_role
from hub_server.errors import HubError
from hub_server.models import Job
from hub_server.services.jobs import JobService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(tags=["logs-stream"], dependencies=[Depends(require_role("viewer"))])

_POLL_INTERVAL_SECONDS = 1.0
_STREAM_MAX_SECONDS = 300.0
_LOG_LIMIT = 100
_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT"}


@router.get("/jobs/{job_key}/logs/stream")
def stream_job_logs(
    job_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> StreamingResponse:
    """Stream runner events for one Job until it reaches a terminal state."""
    job = session.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        raise HubError(code="JOB_NOT_FOUND", message="Job 不存在", status_code=404)
    return StreamingResponse(
        _event_stream(session, storage, settings, job_key),
        media_type="text/event-stream",
    )


def _event_stream(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    job_key: str,
) -> Iterator[str]:
    service = JobService(session, storage, settings)
    cursor = 0
    deadline = time.monotonic() + _STREAM_MAX_SECONDS
    try:
        while True:
            try:
                items, next_cursor = service.logs(job_key, cursor=cursor, limit=_LOG_LIMIT)
            except HubError:
                # The Job vanished mid-stream; close the channel gracefully.
                yield _sse_event("end", {})
                return
            for item in items:
                yield _sse_event("log", item)
            if next_cursor is not None:
                cursor = next_cursor
            elif items:
                # Read to EOF: keep the position so already-sent lines are not re-sent.
                cursor += len(items)
            # Release the read snapshot so each poll observes fresh Job state.
            session.commit()
            if next_cursor is None and _is_terminal(session, job_key):
                yield _sse_event("end", {})
                return
            if time.monotonic() >= deadline:
                yield _sse_event("end", {})
                return
            time.sleep(_POLL_INTERVAL_SECONDS)
    except (GeneratorExit, ConnectionResetError):
        # Client disconnects: stop polling immediately without emitting anything.
        return


def _is_terminal(session: Session, job_key: str) -> bool:
    job = session.scalar(select(Job).where(Job.job_key == job_key))
    return job is not None and job.status in _TERMINAL_STATUSES


def _sse_event(event: str, data: object) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("\r", " ").replace("\n", " ")
    return f"event: {event}\ndata: {payload}\n\n"
