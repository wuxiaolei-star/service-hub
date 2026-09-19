"""Audit trail endpoints (admin only)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import require_role
from hub_server.models import AuditLogRecord

router = APIRouter(
    prefix="/audit-logs", tags=["audit-logs"], dependencies=[Depends(require_role("admin"))]
)

_AUDIT_CSV_HEADER: tuple[str, ...] = (
    "at",
    "actor_type",
    "actor_name",
    "action",
    "resource_type",
    "resource_id",
    "result",
    "ip",
)
_EXPORT_BATCH_SIZE = 500
_CSV_INJECTION_PREFIXES = ("=", "+", "-", "@")


def _entry_response(entry: AuditLogRecord) -> dict[str, object]:
    return {
        "id": entry.id,
        "at": entry.at.isoformat() if entry.at else None,
        "actor_type": entry.actor_type,
        "actor_id": entry.actor_id,
        "actor_name": entry.actor_name,
        "action": entry.action,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "detail": entry.detail,
        "ip": entry.ip,
        "result": entry.result,
    }


@router.get("")
def list_audit_logs(
    session: Annotated[Session, Depends(get_session)],
    action: str | None = Query(default=None),
    actor_name: str | None = Query(default=None),
    limit: int = Query(default=100, gt=0, le=500),
) -> dict[str, object]:
    """Return newest audit entries with optional action/actor filtering."""
    query = session.query(AuditLogRecord).order_by(AuditLogRecord.id.desc())
    if action is not None:
        query = query.filter(AuditLogRecord.action == action)
    if actor_name is not None:
        query = query.filter(AuditLogRecord.actor_name == actor_name)
    entries = query.limit(limit).all()
    return {"items": [_entry_response(entry) for entry in entries]}


@router.get("/export", response_class=StreamingResponse)
def export_audit_logs(
    session: Annotated[Session, Depends(get_session)],
) -> StreamingResponse:
    """Stream every audit entry as a CSV attachment for spreadsheet consumers."""
    return StreamingResponse(
        _audit_csv_rows(session),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit-logs.csv"'},
    )


def _audit_csv_rows(session: Session) -> Iterator[str]:
    """Stream the header line plus all entries in ascending time order, in batches."""
    yield _csv_line(_AUDIT_CSV_HEADER)
    offset = 0
    while True:
        batch = session.scalars(
            select(AuditLogRecord)
            .order_by(AuditLogRecord.at.asc(), AuditLogRecord.id.asc())
            .offset(offset)
            .limit(_EXPORT_BATCH_SIZE)
        ).all()
        for entry in batch:
            yield _csv_line(
                (
                    entry.at.isoformat() if entry.at else "",
                    entry.actor_type,
                    entry.actor_name,
                    entry.action,
                    entry.resource_type,
                    entry.resource_id,
                    entry.result,
                    entry.ip,
                )
            )
        if len(batch) < _EXPORT_BATCH_SIZE:
            return
        offset += len(batch)


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
