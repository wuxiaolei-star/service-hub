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

_LOGGER = logging.getLogger("hub.scheduler")
_STOP = {"requested": False}

_TASKS: list[Callable[[object], int]] = []


def _register_default_tasks() -> None:
    """Import the automation services lazily so tests can run without them."""
    from hub_server.services.pipelines import advance_runs
    from hub_server.services.schedules import trigger_due
    from hub_server.services.webhooks import deliver_due

    if not _TASKS:
        register_task(deliver_due)
        register_task(trigger_due)
        register_task(advance_runs)


def register_task(task: Callable[[object], int]) -> None:
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
    _register_default_tasks()

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
