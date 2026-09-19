"""Scheduler-side reconciliation of desired long-running service state.

The services API records what an operator asked for (``desired_state``) but the
Docker daemon can still take a container down out of band: an OOM kill, a host
reboot, a crashed process. The scheduler therefore re-checks every definition
that should be running and starts the ones that drifted, so a wedged service is
corrected within one sweep instead of waiting for a human to notice.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import ServiceDef
from hub_server.services.audit import record as audit


class _ManagerClient(Protocol):
    """The slice of the service-manager client the reconciler depends on."""

    def status(self, name: str) -> dict[str, object]: ...

    def action(self, name: str, action: str) -> dict[str, object]: ...


def reconcile_services(
    session: Session,
    client: _ManagerClient,
    now: datetime | None = None,
) -> int:
    """Start containers whose state drifted from ``desired_state == RUNNING``.

    Returns the number of successful corrections. Every correction (and every
    failed start attempt) writes one ``service.reconcile`` audit entry attributed
    to the system scheduler. A manager that cannot answer for a definition
    (502 unavailable, or 404 container gone — reconcile restarts, it never
    redeploys) is skipped without a per-entry entry; all skipped definitions are
    reported in one aggregated ``denied`` entry per pass so a dead manager
    cannot flood the audit log. ``now`` is accepted for signature parity with
    the other sweep tasks and is currently unused.
    """
    del now
    definitions = session.scalars(
        select(ServiceDef)
        .where(ServiceDef.desired_state == "RUNNING")
        .order_by(ServiceDef.name)
    ).all()
    corrections = 0
    skipped: list[dict[str, object]] = []
    for definition in definitions:
        try:
            reported = client.status(definition.name)
        except HubError as error:
            if error.status_code in (502, 404):
                skipped.append({"name": definition.name, "code": error.code})
                continue
            raise
        state = reported.get("state")
        if state == "running":
            continue
        try:
            client.action(definition.name, "start")
        except HubError as error:
            _audit_reconcile(
                session,
                definition,
                result="denied",
                detail={
                    "name": definition.name,
                    "previous_state": _readable_state(state),
                    "code": error.code,
                    "message": error.message,
                },
            )
            session.commit()
            continue
        _audit_reconcile(
            session,
            definition,
            result="ok",
            detail={"name": definition.name, "previous_state": _readable_state(state)},
        )
        session.commit()
        corrections += 1
    if skipped:
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="scheduler",
            action="service.reconcile",
            resource_type="service",
            resource_id=None,
            detail={"reason": "status_unavailable", "skipped": skipped},
            result="denied",
        )
        session.commit()
    return corrections


def _readable_state(state: object) -> str:
    return state if isinstance(state, str) else "unknown"


def _audit_reconcile(
    session: Session,
    definition: ServiceDef,
    *,
    result: str,
    detail: dict[str, object],
) -> None:
    audit(
        session,
        actor_type="system",
        actor_id=None,
        actor_name="scheduler",
        action="service.reconcile",
        resource_type="service",
        resource_id=str(definition.id),
        detail=detail,
        result=result,
    )
