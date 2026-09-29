"""Server-Sent Events streaming for one Job's runner log."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from hub_server.dependencies import get_storage
from hub_server.dependencies_auth import require_role
from hub_server.errors import HubError
from hub_server.models import Job
from hub_server.services.job_events import (
    decode_event_line,
    resolve_event_log_path,
)
from hub_server.storage import LocalStorage

router = APIRouter(tags=["logs-stream"], dependencies=[Depends(require_role("viewer"))])

_POLL_INTERVAL_SECONDS = 1.0
_STREAM_MAX_SECONDS = 300.0
_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT"}


@router.get("/jobs/{job_key}/logs/stream")
def stream_job_logs(
    job_key: str,
    request: Request,
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> StreamingResponse:
    """Stream runner events for one Job until it reaches a terminal state."""
    session_factory = request.app.state.session_factory
    # Short-lived lookup only: this route must not pin a request-scoped session
    # (and its connection) for the whole streaming lifetime.
    with session_factory() as session:
        job = session.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        raise HubError(code="JOB_NOT_FOUND", message="Job 不存在", status_code=404)
    return StreamingResponse(
        _event_stream(session_factory, storage, job_key, job.workspace_path),
        media_type="text/event-stream",
    )


def _event_stream(
    session_factory: sessionmaker[Session],
    storage: LocalStorage,
    job_key: str,
    workspace_path: str | None,
) -> Iterator[str]:
    """Tail the Job's ``meta/events.jsonl`` by byte offset until it settles.

    Jobs from before the audit-M-2 relocation still stream from their legacy
    ``logs/events.jsonl`` (see :func:`resolve_event_log_path`). Unparsable
    lines — possible in hand-migrated legacy files — are skipped instead of
    tearing down the stream.
    """
    deadline = time.monotonic() + _STREAM_MAX_SECONDS
    try:
        log_path = _log_path(storage, workspace_path)
    except HubError:
        # Storage rejected the workspace path; close the channel gracefully.
        yield _sse_event("end", {})
        return
    handle: BinaryIO | None = None
    offset = 0
    pending = b""
    try:
        while True:
            # Borrow one fresh session per poll so the SQLite connection and its
            # read snapshot are held only for the duration of this status query.
            with session_factory() as session:
                job = _load_job(session, job_key)
            if job is None:
                # The Job vanished mid-stream; close the channel gracefully.
                yield _sse_event("end", {})
                return
            if handle is None and log_path is not None and log_path.exists():
                # Unbuffered so every poll reads fresh bytes straight from the file
                # instead of re-serving a userspace buffer past the seek offset.
                handle = log_path.open("rb", buffering=0)
            if handle is not None:
                handle.seek(offset)
                chunk = handle.read()
                if chunk:
                    pending += chunk
            if b"\n" in pending:
                complete_lines, _, pending = pending.rpartition(b"\n")
                # Advance only to the last complete line so a torn final line is
                # never re-sent once its remainder arrives.
                offset += len(complete_lines) + 1
                for raw_line in complete_lines.split(b"\n"):
                    item = decode_event_line(raw_line)
                    if item is not None:
                        yield _sse_event("log", item)
            if job.status in _TERMINAL_STATUSES:
                if pending:
                    # The writer is finished and this tail never got its newline;
                    # emit the fragment exactly like the previous full-file read.
                    item = decode_event_line(pending)
                    pending = b""
                    if item is not None:
                        yield _sse_event("log", item)
                yield _sse_event("end", {})
                return
            if time.monotonic() >= deadline:
                yield _sse_event("end", {})
                return
            time.sleep(_POLL_INTERVAL_SECONDS)
    except (GeneratorExit, ConnectionResetError):
        # Client disconnects: stop polling immediately without emitting anything.
        return
    finally:
        if handle is not None:
            handle.close()


def _log_path(storage: LocalStorage, workspace_path: str | None) -> Path | None:
    if workspace_path is None:
        return None
    return resolve_event_log_path(storage, workspace_path)


def _load_job(session: Session, job_key: str) -> Job | None:
    return session.scalar(select(Job).where(Job.job_key == job_key))


def _sse_event(event: str, data: object) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("\r", " ").replace("\n", " ")
    return f"event: {event}\ndata: {payload}\n\n"
