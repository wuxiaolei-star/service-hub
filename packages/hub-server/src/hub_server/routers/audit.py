"""Read-only audit trail endpoint (admin only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import require_role
from hub_server.models import AuditLogRecord

router = APIRouter(
    prefix="/audit-logs", tags=["audit-logs"], dependencies=[Depends(require_role("admin"))]
)


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
