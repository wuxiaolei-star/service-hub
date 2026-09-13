"""Audit trail persistence shared by all write operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from hub_server.models import AuditLogRecord


def record(
    session: Session,
    *,
    actor_type: str,
    actor_id: int | None,
    actor_name: str,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, object] | None = None,
    ip: str | None = None,
    result: str = "ok",
) -> None:
    """Persist one audited operation and flush so callers control commit timing."""
    session.add(
        AuditLogRecord(
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip=ip,
            result=result,
        )
    )
    session.flush()
