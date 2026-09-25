"""Standalone retention sweeper process managed by supervisord."""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

from hub_server.db import create_engine_and_session_factory
from hub_server.services.environments import scan_environment_orphans
from hub_server.services.retention import RetentionSweeper
from hub_server.settings import HubSettings

_LOGGER = logging.getLogger("hub.cleaner")
_STOP = {"requested": False}


def _handle_signal(signum: int, _frame: object) -> None:
    _LOGGER.info("cleaner received signal %s, exiting after current sweep", signum)
    _STOP["requested"] = True


def main(once: bool = False) -> int:
    """Run retention sweeps forever (or once for smoke testing)."""
    logging.basicConfig(level=logging.INFO)
    settings = HubSettings.from_yaml(Path(os.environ["HUB_CONFIG_PATH"]))
    _engine, session_factory = create_engine_and_session_factory(settings.database.url)
    signal.signal(signal.SIGTERM, _handle_signal)

    interval_seconds = settings.retention.sweep_interval_minutes * 60
    _LOGGER.info(
        "cleaner started: ttl=%sh interval=%smin",
        settings.retention.input_ttl_hours,
        settings.retention.sweep_interval_minutes,
    )
    while True:
        try:
            with session_factory() as session:
                sweeper = RetentionSweeper(
                    session, Path(settings.storage.root), settings.retention
                )
                deleted = sweeper.sweep()
            if deleted:
                _LOGGER.info("sweep deleted %s expired input files", deleted)
        except Exception:
            _LOGGER.exception("retention sweep failed")
        try:
            # Read-only guard scan on the same cadence as the retention sweep:
            # reports environments/ directories that reference no Build. It
            # deletes nothing — guarded deletion is owned by B4b — and mounting
            # it here (rather than the 30s scheduler tick) needs no extra
            # throttle state because the cleaner already wakes at
            # retention.sweep_interval_minutes.
            with session_factory() as session:
                scan_environment_orphans(session, Path(settings.storage.root))
        except Exception:
            _LOGGER.exception("environments orphan scan failed")
        if once or _STOP["requested"]:
            break
        time.sleep(interval_seconds)
    _LOGGER.info("cleaner stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main(once="--once" in sys.argv))
