"""Scheduler process for callbacks, schedules, and pipeline advancement."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path

from hub_server.db import create_engine_and_session_factory
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage
from sqlalchemy.orm import Session

_LOGGER = logging.getLogger("hub.scheduler")
_STOP = {"requested": False}

_TASKS: list[Callable[[Session], int]] = []


def _register_default_tasks(settings: HubSettings) -> None:
    """Bind storage, settings, and the webhook policy into session-only sweep tasks.

    The automation services need more than a session: schedule triggering and
    pipeline advancement create Jobs (storage + settings) and callback delivery
    needs the outbound SSRF policy. Service reconciliation talks to the isolated
    service-manager process, so its loopback client is built once here from
    ``settings.runner.shared_token`` and the manager URL (port) injected into the
    environment, then closed over. Adapters keep ``register_task`` signatures
    honest, so a mismatch surfaces here instead of as a swallowed TypeError in
    every sweep.
    """
    from hub_server.services.pipelines import advance_runs
    from hub_server.services.reaper import reap_stale_jobs
    from hub_server.services.reconcile import reconcile_services
    from hub_server.services.retention import (
        sweep_expired_sessions,
        sweep_old_audit_logs,
        sweep_terminal_jobs,
    )
    from hub_server.services.schedules import trigger_due
    from hub_server.services.service_manager import get_service_manager
    from hub_server.services.webhooks import deliver_due

    if _TASKS:
        return
    storage = LocalStorage(Path(settings.storage.root))
    manager = get_service_manager(settings)

    def deliver_callbacks(session: Session) -> int:
        return deliver_due(session, policy=settings.webhooks)

    def trigger_schedules(session: Session) -> int:
        return trigger_due(session, storage, settings)

    def advance_pipelines(session: Session) -> int:
        return advance_runs(session, storage, settings)

    def delete_retired_jobs(session: Session) -> int:
        return sweep_terminal_jobs(session, Path(settings.storage.root), settings.retention)

    def delete_expired_sessions(session: Session) -> int:
        return sweep_expired_sessions(session)

    def delete_old_audit_logs(session: Session) -> int:
        return sweep_old_audit_logs(session, settings.retention)

    def reap_lost_jobs(session: Session) -> int:
        return reap_stale_jobs(session)

    def reconcile_service_containers(session: Session) -> int:
        return reconcile_services(session, manager)

    register_task(deliver_callbacks)
    register_task(trigger_schedules)
    register_task(advance_pipelines)
    register_task(reap_lost_jobs)
    register_task(reconcile_service_containers)
    register_task(delete_retired_jobs)
    register_task(delete_expired_sessions)
    register_task(delete_old_audit_logs)


def register_task(task: Callable[[Session], int]) -> None:
    """Register one sweep task; it receives a session and returns work count."""
    _TASKS.append(task)


def _handle_signal(signum: int, _frame: object) -> None:
    _LOGGER.info("scheduler received signal %s, stopping", signum)
    _STOP["requested"] = True


def main(once: bool = False) -> int:
    """Run registered automation sweeps every 30 seconds (or once)."""
    logging.basicConfig(level=logging.INFO)
    settings = HubSettings.from_yaml(Path(os.environ["HUB_CONFIG_PATH"]))
    _engine, session_factory = create_engine_and_session_factory(settings.database.url)
    signal.signal(signal.SIGTERM, _handle_signal)
    _register_default_tasks(settings)

    _LOGGER.info("scheduler started with %s tasks", len(_TASKS))
    while True:
        for task in _TASKS:
            try:
                with session_factory() as session:
                    worked = task(session)
                if worked:
                    _LOGGER.info("task %s processed %s items", task.__name__, worked)
            except Exception:
                _LOGGER.exception("automation task %s failed", getattr(task, "__name__", task))
        if once or _STOP["requested"]:
            break
        time.sleep(30)
    _LOGGER.info("scheduler stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(once="--once" in sys.argv))
