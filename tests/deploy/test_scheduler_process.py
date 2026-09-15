"""Scheduler process wiring tests: registry shape and a real single sweep."""

from __future__ import annotations

import inspect
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "deploy"))

import pytest
from hub_server.db import Base, create_engine_and_session_factory
from hub_server.models import AuditLogRecord, Schedule
from hub_server.settings import (
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from service_hub import scheduler_service


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
    )


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"


def _write_config(tmp_path: Path, database_url: str) -> Path:
    config = tmp_path / "hub.yaml"
    config.write_text(
        "deployment:\n  mode: offline\n"
        f"storage:\n  root: {(tmp_path / 'data').as_posix()}\n"
        f"database:\n  url: {database_url}\n"
        "uploads:\n  max_size_bytes: 1024\n"
        "runner:\n  shared_token: x\n"
        "webhooks:\n  allow_private_networks: false\n  allowed_hosts: []\n",
        encoding="utf-8",
    )
    return config


def _seed_due_schedule(database_url: str) -> None:
    engine, session_factory = create_engine_and_session_factory(database_url)
    Base.metadata.create_all(engine)
    with session_factory() as session:
        session.add(
            Schedule(
                name="nightly",
                plugin_id="missing_plugin",
                version="1.0.0",
                runtime_type="docker",
                inputs_json={},
                params_json={},
                interval_minutes=60,
                enabled=True,
                next_run_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
        session.commit()
    engine.dispose()


def test_supervisord_manages_scheduler_process() -> None:
    config = Path("deploy/service_hub/supervisord.conf").read_text("utf-8")
    assert "[program:scheduler]" in config
    assert "scheduler_service" in config


def test_registered_tasks_accept_only_a_session(tmp_path: Path) -> None:
    """A missing storage/settings argument used to fail every sweep with TypeError."""
    scheduler_service._TASKS.clear()

    scheduler_service._register_default_tasks(_settings(tmp_path))

    names = [task.__name__ for task in scheduler_service._TASKS]
    assert names == ["deliver_callbacks", "trigger_schedules", "advance_pipelines"]
    for task in scheduler_service._TASKS:
        inspect.signature(task).bind(object())


def test_once_mode_runs_every_sweep_without_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    database_url = _database_url(tmp_path)
    _seed_due_schedule(database_url)
    monkeypatch.setenv("HUB_CONFIG_PATH", str(_write_config(tmp_path, database_url)))
    scheduler_service._TASKS.clear()

    with caplog.at_level(logging.ERROR, logger="hub.scheduler"):
        exit_code = scheduler_service.main(once=True)

    assert exit_code == 0
    assert [record.getMessage() for record in caplog.records] == []


def test_once_mode_triggers_a_due_schedule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The sweep must reach the real service, not just survive the call."""
    database_url = _database_url(tmp_path)
    _seed_due_schedule(database_url)
    monkeypatch.setenv("HUB_CONFIG_PATH", str(_write_config(tmp_path, database_url)))
    scheduler_service._TASKS.clear()

    assert scheduler_service.main(once=True) == 0

    engine, session_factory = create_engine_and_session_factory(database_url)
    with session_factory() as session:
        entries = session.query(AuditLogRecord).filter_by(action="schedule.trigger").all()
    engine.dispose()
    assert len(entries) == 1
    assert entries[0].result == "denied"
    assert entries[0].detail is not None
    assert entries[0].detail["code"] == "PLUGIN_BUILD_NOT_ENABLED"
