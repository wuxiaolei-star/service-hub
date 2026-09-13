"""Scheduler trigger_due behavior tests against the session-level service."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import (
    AuditLogRecord,
    Environment,
    Job,
    Plugin,
    PluginBuild,
    PluginVersion,
    Schedule,
)
from hub_server.services.schedules import trigger_due
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from hub_server.storage import LocalStorage
from sqlalchemy.orm import Session


@pytest.fixture
def environment(tmp_path: Path) -> Iterator[tuple[Session, LocalStorage, HubSettings]]:
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="off"),
        quotas=QuotasSettings(),
    )
    app = create_app(settings)
    with TestClient(app) as client, app.state.session_factory() as database_session:
        yield database_session, client.app.state.storage, settings


def test_trigger_due_creates_job_and_advances_schedule(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    _seed_build(session)
    schedule = _seed_schedule(
        session, name="due", next_run_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    seeded_next_run_at = schedule.next_run_at

    triggered = trigger_due(session, storage, settings, now=datetime.now(UTC))

    assert triggered == 1
    refreshed = _reload_schedule(session, schedule.id)
    job = session.query(Job).one()
    assert refreshed.last_job_id == job.job_key
    assert refreshed.next_run_at == seeded_next_run_at + timedelta(
        minutes=refreshed.interval_minutes
    )
    entry = session.query(AuditLogRecord).filter_by(action="schedule.trigger").one()
    assert entry.result == "ok"
    assert entry.actor_type == "system"
    assert entry.actor_name == "scheduler"
    assert entry.resource_type == "schedule"
    assert entry.resource_id == job.job_key


def test_trigger_due_defaults_to_current_time(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    _seed_build(session)
    _seed_schedule(session, name="past", next_run_at=datetime.now(UTC) - timedelta(minutes=1))

    assert trigger_due(session, storage, settings) == 1
    assert session.query(Job).count() == 1


def test_trigger_due_skips_not_yet_due_schedule(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    _seed_build(session)
    schedule = _seed_schedule(
        session, name="future", next_run_at=datetime.now(UTC) + timedelta(hours=1)
    )
    seeded_next_run_at = schedule.next_run_at

    triggered = trigger_due(session, storage, settings, now=datetime.now(UTC))

    assert triggered == 0
    assert session.query(Job).count() == 0
    assert session.query(AuditLogRecord).count() == 0
    refreshed = _reload_schedule(session, schedule.id)
    assert refreshed.next_run_at == seeded_next_run_at
    assert refreshed.last_job_id is None


def test_trigger_due_skips_disabled_schedule(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    _seed_build(session)
    _seed_schedule(
        session,
        name="disabled",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        enabled=False,
    )

    triggered = trigger_due(session, storage, settings, now=datetime.now(UTC))

    assert triggered == 0
    assert session.query(Job).count() == 0


def test_trigger_due_denies_schedule_without_enabled_build(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    schedule = _seed_schedule(
        session,
        name="ghost",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        plugin_id="missing_plugin",
    )
    seeded_next_run_at = schedule.next_run_at

    triggered = trigger_due(session, storage, settings, now=datetime.now(UTC))

    assert triggered == 0
    assert session.query(Job).count() == 0
    refreshed = _reload_schedule(session, schedule.id)
    assert refreshed.last_job_id is None
    assert refreshed.next_run_at == seeded_next_run_at
    entry = session.query(AuditLogRecord).filter_by(action="schedule.trigger").one()
    assert entry.result == "denied"
    assert entry.detail is not None
    assert entry.detail["code"] == "PLUGIN_BUILD_NOT_ENABLED"


def test_trigger_due_isolates_failing_schedules(
    environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = environment
    _seed_build(session)
    good = _seed_schedule(
        session, name="good", next_run_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    _seed_schedule(
        session,
        name="bad",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        plugin_id="missing_plugin",
    )

    triggered = trigger_due(session, storage, settings, now=datetime.now(UTC))

    assert triggered == 1
    entries = session.query(AuditLogRecord).filter_by(action="schedule.trigger").all()
    assert sorted(entry.result for entry in entries) == ["denied", "ok"]
    assert session.query(Job).count() == 1
    assert _reload_schedule(session, good.id).last_job_id is not None


def _reload_schedule(session: Session, schedule_id: int) -> Schedule:
    session.expire_all()
    refreshed = session.get(Schedule, schedule_id)
    assert refreshed is not None
    return refreshed


def _seed_schedule(
    session: Session,
    *,
    name: str,
    next_run_at: datetime,
    enabled: bool = True,
    plugin_id: str = "nc_to_shp",
    version: str = "1.0.0",
    runtime_type: str = "docker",
    interval_minutes: int = 60,
) -> Schedule:
    schedule = Schedule(
        name=name,
        plugin_id=plugin_id,
        version=version,
        runtime_type=runtime_type,
        inputs_json={},
        params_json={},
        interval_minutes=interval_minutes,
        enabled=enabled,
        next_run_at=next_run_at,
    )
    session.add(schedule)
    session.commit()
    return schedule


def _seed_build(
    session: Session,
    *,
    plugin_key: str = "nc_to_shp",
    version: str = "1.0.0",
    runtime_type: str = "docker",
) -> None:
    plugin = Plugin(plugin_key=plugin_key, name="NC to Shapefile")
    plugin_version = PluginVersion(
        plugin=plugin,
        version=version,
        spec_version="1.0",
        sdk_version="1.0",
        source_sha256="a" * 64,
        manifest_json=_manifest(plugin_key=plugin_key, version=version),
        status="INSTALLED",
    )
    fingerprint = "d" * 64
    build = PluginBuild(
        build_key=f"plugin_build_{runtime_type.replace('-', '_')}",
        manifest_build_id=f"build-{runtime_type}",
        plugin_version=plugin_version,
        target_os="linux",
        target_arch="amd64",
        runtime_type=runtime_type,
        python_version="3.12",
        sdk_version="1.0",
        package_path=f"plugins/plugin_build_{runtime_type.replace('-', '_')}",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="image.tar.zst",
        runtime_fingerprint=fingerprint,
        build_metadata_json={
            "schema_version": "1.0",
            "build_id": f"build-{runtime_type}",
            "plugin_id": plugin_key,
            "plugin_version": version,
            "target": {"os": "linux", "arch": "amd64"},
            "python_version": "3.12",
            "runtime": {"type": "docker", "archive": "image.tar.zst", "digest": fingerprint},
            "sdk_version": "1.0.0",
            "source_sha256": "a" * 64,
            "built_at": datetime.now(UTC).isoformat(),
        },
        status="ENABLED",
    )
    environment = Environment(
        plugin_build=build,
        runtime_type=runtime_type,
        fingerprint=fingerprint,
        image_digest=fingerprint,
        metadata_json={"type": "docker", "digest": fingerprint},
        status="READY",
    )
    session.add_all([plugin, plugin_version, build, environment])
    session.commit()


def _manifest(*, plugin_key: str, version: str) -> dict[str, object]:
    return {
        "spec_version": "1.0",
        "plugin": {"id": plugin_key, "name": "NC to Shapefile", "version": version},
        "sdk": {"version": "1.0"},
        "runtime": {"type": "process", "python": {"version": "3.12"}},
        "entrypoint": {"module": "nc_to_shp_plugin.main", "function": "run"},
        "parameters": [],
        "inputs": [],
        "outputs": [],
        "execution": {"timeout": 30, "concurrency": 1},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }
