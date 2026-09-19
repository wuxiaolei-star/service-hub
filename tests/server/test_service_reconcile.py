"""Scheduler reconciliation of desired service container state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from hub_server.errors import HubError
from hub_server.models import AuditLogRecord, ServiceDef
from hub_server.services.reconcile import reconcile_services
from sqlalchemy.orm import Session


class FakeManager:
    """Manager double that records status/start calls and replays canned answers."""

    def __init__(
        self,
        *,
        states: dict[str, str] | None = None,
        status_error: HubError | None = None,
        start_error: HubError | None = None,
    ) -> None:
        self.status_calls: list[str] = []
        self.start_calls: list[str] = []
        self._states = states or {}
        self._status_error = status_error
        self._start_error = start_error

    def status(self, name: str) -> dict[str, object]:
        self.status_calls.append(name)
        if self._status_error is not None:
            raise self._status_error
        return {"name": f"hub-svc-{name}", "state": self._states.get(name, "running")}

    def action(self, name: str, action: str) -> dict[str, object]:
        self.start_calls.append(f"{name}:{action}")
        if self._start_error is not None:
            raise self._start_error
        return {"name": f"hub-svc-{name}", "state": "running"}


def _unavailable() -> HubError:
    return HubError(
        code="SERVICE_MANAGER_UNAVAILABLE", message="服务管理器不可用", status_code=502
    )


def _definition(session: Session, name: str, desired_state: str) -> ServiceDef:
    definition = ServiceDef(
        name=name,
        image="ghcr.io/example/web:1",
        container_name=f"hub-svc-{name}",
        ports_json=[],
        env_json={},
        mounts_json=[],
        desired_state=desired_state,
    )
    session.add(definition)
    session.commit()
    return definition


def _reconcile_actions(session: Session) -> list[AuditLogRecord]:
    return session.query(AuditLogRecord).filter_by(action="service.reconcile").all()


def test_running_container_needs_no_correction(session: Session) -> None:
    _definition(session, "web", "RUNNING")
    manager = FakeManager(states={"web": "running"})

    corrected = reconcile_services(session, manager)

    assert corrected == 0
    assert manager.status_calls == ["web"]
    assert manager.start_calls == []
    assert _reconcile_actions(session) == []


def test_exited_container_is_started_and_audited(session: Session) -> None:
    definition = _definition(session, "web", "RUNNING")
    manager = FakeManager(states={"web": "exited"})

    corrected = reconcile_services(session, manager)

    assert corrected == 1
    assert manager.start_calls == ["web:start"]
    entries = _reconcile_actions(session)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.actor_type == "system"
    assert entry.actor_id is None
    assert entry.actor_name == "scheduler"
    assert entry.resource_type == "service"
    assert entry.resource_id == str(definition.id)
    assert entry.result == "ok"
    detail: dict[str, Any] = entry.detail or {}
    assert detail["name"] == "web"
    assert detail["previous_state"] == "exited"


def test_stopped_definitions_are_skipped(session: Session) -> None:
    _definition(session, "web", "STOPPED")

    corrected = reconcile_services(session, FakeManager())

    assert corrected == 0
    assert _reconcile_actions(session) == []


def test_manager_unavailable_skips_entries_without_crashing(session: Session) -> None:
    _definition(session, "web", "RUNNING")
    _definition(session, "api", "RUNNING")
    manager = FakeManager(status_error=_unavailable())

    corrected = reconcile_services(session, manager)

    assert corrected == 0
    assert manager.start_calls == []
    entries = _reconcile_actions(session)
    assert len(entries) == 1  # one aggregated denial, none per entry
    entry = entries[0]
    assert entry.result == "denied"
    assert entry.actor_name == "scheduler"
    skipped: Iterable[dict[str, Any]] = (entry.detail or {}).get("skipped", [])
    assert {item["name"] for item in skipped} == {"web", "api"}


def test_start_failure_is_audited_as_denied(session: Session) -> None:
    _definition(session, "web", "RUNNING")
    manager = FakeManager(
        states={"web": "exited"},
        start_error=HubError(code="SERVICE_MANAGER_REJECTED", message="拒绝", status_code=502),
    )

    corrected = reconcile_services(session, manager)

    assert corrected == 0
    assert manager.start_calls == ["web:start"]
    entries = _reconcile_actions(session)
    assert len(entries) == 1
    assert entries[0].result == "denied"
    assert (entries[0].detail or {})["name"] == "web"


def test_missing_container_is_skipped_and_reported(session: Session) -> None:
    """A 404 means the container is gone; reconcile restarts, never redeploys."""
    _definition(session, "web", "RUNNING")
    manager = FakeManager(
        status_error=HubError(
            code="SERVICE_CONTAINER_NOT_FOUND", message="服务容器不存在", status_code=404
        )
    )

    corrected = reconcile_services(session, manager)

    assert corrected == 0
    assert manager.start_calls == []
    entries = _reconcile_actions(session)
    assert len(entries) == 1
    assert entries[0].result == "denied"
