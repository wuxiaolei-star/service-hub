"""Cron-expression schedules: API creation contract and scheduler trigger_due."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from croniter import croniter
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
    UserRecord,
)
from hub_server.routers.schedules import router as schedules_router
from hub_server.services.auth import AuthService
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

HOURLY = "0 * * * *"
# Fixed evaluation instant so cron expectations are deterministic.
NOW = datetime(2026, 9, 19, 10, 47, 30, tzinfo=UTC)


def _api_settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_api_settings(tmp_path))
    app.include_router(schedules_router, prefix="/api/v1")
    return TestClient(app)


def _headers_for(client: TestClient, username: str) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _operator_headers(client: TestClient) -> dict[str, str]:
    admin = _headers_for(client, "admin")
    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "ops", "password": "long-enough-pass", "role": "operator"},
    )
    assert created.status_code == 201, created.text
    return _headers_for(client, "ops")


def _cron_payload(name: str = "nightly-cron", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "plugin_id": "nc_to_shp",
        "version": "1.0.0",
        "runtime_type": "docker",
        "inputs": {},
        "params": {"group_name": "1"},
        "cron_expr": "30 2 * * *",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# API contract (POST /schedules, GET /schedules)
# ---------------------------------------------------------------------------


def test_create_cron_schedule_computes_next_run_from_cron(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _operator_headers(client)
        before = datetime.now(UTC)

        response = client.post(
            "/api/v1/schedules", headers=headers, json=_cron_payload()
        )
        after = datetime.now(UTC)

        assert response.status_code == 201, response.text
        row = response.json()
        assert row["cron_expr"] == "30 2 * * *"
        assert row["missed_run_policy"] == "skip"
        assert row["interval_minutes"] == 0
        assert row["enabled"] is True
        # The next run must be the first cron fire inside the request window.
        earliest = croniter("30 2 * * *", before).get_next(datetime)
        latest = croniter("30 2 * * *", after).get_next(datetime)
        next_run_at = datetime.fromisoformat(row["next_run_at"])
        assert earliest <= next_run_at <= latest

        with client.app.state.session_factory() as session:
            record = session.query(Schedule).filter(Schedule.name == "nightly-cron").one()
            assert record.cron_expr == "30 2 * * *"
            assert record.missed_run_policy == "skip"


def test_create_persists_chosen_missed_run_policy(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _operator_headers(client)
        response = client.post(
            "/api/v1/schedules",
            headers=headers,
            json=_cron_payload(missed_run_policy="catch_up"),
        )
        assert response.status_code == 201, response.text

        listed = client.get("/api/v1/schedules", headers=headers)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["cron_expr"] == "30 2 * * *"
        assert items[0]["missed_run_policy"] == "catch_up"


def test_create_rejects_missing_trigger_mode(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _operator_headers(client)

        response = client.post(
            "/api/v1/schedules",
            headers=headers,
            json=_cron_payload(cron_expr=None),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SCHEDULE_TRIGGER_MODE_REQUIRED"


def test_create_rejects_conflicting_trigger_modes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _operator_headers(client)

        response = client.post(
            "/api/v1/schedules",
            headers=headers,
            json=_cron_payload(interval_minutes=60),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SCHEDULE_TRIGGER_MODE_CONFLICT"


def test_create_rejects_invalid_cron_expr(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _operator_headers(client)

        response = client.post(
            "/api/v1/schedules",
            headers=headers,
            json=_cron_payload(cron_expr="not a cron"),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SCHEDULE_INVALID_CRON_EXPR"


# ---------------------------------------------------------------------------
# Scheduler behavior (trigger_due)
# ---------------------------------------------------------------------------


def test_trigger_due_runs_due_cron_schedule(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    _seed_build(session)
    _seed_cron_schedule(
        session,
        name="cron-due",
        cron_expr="*/10 * * * *",
        next_run_at=NOW.replace(minute=40, second=0, microsecond=0),
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 1
    job = session.query(Job).one()
    refreshed = _reload_schedule(session)
    assert refreshed.last_job_id == job.job_key
    assert _as_utc(refreshed.next_run_at) == datetime(2026, 9, 19, 10, 50, tzinfo=UTC)
    entry = session.query(AuditLogRecord).filter_by(action="schedule.trigger").one()
    assert entry.result == "ok"
    assert entry.actor_type == "system"
    assert entry.actor_name == "scheduler"
    assert entry.resource_id == job.job_key


def test_trigger_due_skip_policy_runs_once_after_multiple_missed(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    _seed_build(session)
    _seed_cron_schedule(
        session,
        name="cron-skip",
        cron_expr=HOURLY,
        next_run_at=_hours_before(NOW, 5),
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 1
    assert session.query(Job).count() == 1
    refreshed = _reload_schedule(session)
    assert _as_utc(refreshed.next_run_at) == datetime(2026, 9, 19, 11, 0, tzinfo=UTC)


def test_trigger_due_catch_up_runs_each_missed_occurrence(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    _seed_build(session)
    _seed_cron_schedule(
        session,
        name="cron-catch-up",
        cron_expr=HOURLY,
        missed_run_policy="catch_up",
        next_run_at=_hours_before(NOW, 4),
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 5  # 06:00, 07:00, 08:00, 09:00, 10:00
    assert session.query(Job).count() == 5
    refreshed = _reload_schedule(session)
    assert _as_utc(refreshed.next_run_at) == datetime(2026, 9, 19, 11, 0, tzinfo=UTC)
    entries = session.query(AuditLogRecord).filter_by(action="schedule.trigger").all()
    assert len(entries) == 5
    assert all(entry.result == "ok" for entry in entries)


def test_trigger_due_catch_up_caps_missed_runs_at_ten(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    _seed_build(session)
    _seed_cron_schedule(
        session,
        name="cron-burst",
        cron_expr=HOURLY,
        missed_run_policy="catch_up",
        next_run_at=_hours_before(NOW, 15),
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 10  # burst guard: at most 10 jobs per pass
    assert session.query(Job).count() == 10
    refreshed = _reload_schedule(session)
    # The 11th missed fire stays pending and is caught up on the next pass.
    assert _as_utc(refreshed.next_run_at) == datetime(2026, 9, 19, 5, 0, tzinfo=UTC)
    assert _as_utc(refreshed.next_run_at) <= NOW


def test_trigger_due_latest_policy_runs_single_latest_missed(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    _seed_build(session)
    _seed_cron_schedule(
        session,
        name="cron-latest",
        cron_expr=HOURLY,
        missed_run_policy="latest",
        next_run_at=_hours_before(NOW, 5),
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 1
    assert session.query(Job).count() == 1
    refreshed = _reload_schedule(session)
    assert _as_utc(refreshed.next_run_at) == datetime(2026, 9, 19, 11, 0, tzinfo=UTC)


def test_trigger_due_cron_denied_without_enabled_build(
    trigger_environment: tuple[Session, LocalStorage, HubSettings],
) -> None:
    session, storage, settings = trigger_environment
    seeded_next_run_at = NOW.replace(minute=40, second=0, microsecond=0)
    _seed_cron_schedule(
        session,
        name="cron-ghost",
        cron_expr="*/10 * * * *",
        next_run_at=seeded_next_run_at,
        plugin_id="missing_plugin",
    )

    triggered = trigger_due(session, storage, settings, now=NOW)

    assert triggered == 0
    assert session.query(Job).count() == 0
    refreshed = _reload_schedule(session)
    assert _as_utc(refreshed.next_run_at) == seeded_next_run_at
    entry = session.query(AuditLogRecord).filter_by(action="schedule.trigger").one()
    assert entry.result == "denied"
    assert entry.detail is not None
    assert entry.detail["code"] == "PLUGIN_BUILD_NOT_ENABLED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger_settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="off"),
        quotas=QuotasSettings(),
    )


@pytest.fixture
def trigger_environment(
    tmp_path: Path,
) -> Iterator[tuple[Session, LocalStorage, HubSettings]]:
    settings = _trigger_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client, app.state.session_factory() as database_session:
        yield database_session, client.app.state.storage, settings


def _hours_before(reference: datetime, hours: int) -> datetime:
    """The hourly cron fire `hours` before the reference instant."""
    shifted = reference - timedelta(hours=hours)
    return shifted.replace(minute=0, second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _reload_schedule(session: Session) -> Schedule:
    session.expire_all()
    refreshed = session.query(Schedule).one()
    assert refreshed is not None
    return refreshed


def _seed_cron_schedule(
    session: Session,
    *,
    name: str,
    cron_expr: str,
    next_run_at: datetime,
    missed_run_policy: str = "skip",
    enabled: bool = True,
    plugin_id: str = "nc_to_shp",
) -> Schedule:
    schedule = Schedule(
        name=name,
        plugin_id=plugin_id,
        version="1.0.0",
        runtime_type="docker",
        inputs_json={},
        params_json={},
        interval_minutes=0,
        enabled=enabled,
        next_run_at=next_run_at,
        cron_expr=cron_expr,
        missed_run_policy=missed_run_policy,
    )
    session.add(schedule)
    session.commit()
    return schedule


def _seed_build(session: Session) -> None:
    plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
    plugin_version = PluginVersion(
        plugin=plugin,
        version="1.0.0",
        spec_version="1.0",
        sdk_version="1.0",
        source_sha256="a" * 64,
        manifest_json={
            "spec_version": "1.0",
            "plugin": {"id": "nc_to_shp", "name": "NC to Shapefile", "version": "1.0.0"},
            "sdk": {"version": "1.0"},
            "runtime": {"type": "process", "python": {"version": "3.12"}},
            "entrypoint": {"module": "nc_to_shp_plugin.main", "function": "run"},
            "parameters": [],
            "inputs": [],
            "outputs": [],
            "execution": {"timeout": 30, "concurrency": 1},
            "environment_variables": {"required": []},
            "healthcheck": {"enabled": True, "type": "import"},
        },
        status="INSTALLED",
    )
    fingerprint = "d" * 64
    build = PluginBuild(
        build_key="plugin_build_docker",
        manifest_build_id="build-docker",
        plugin_version=plugin_version,
        target_os="linux",
        target_arch="amd64",
        runtime_type="docker",
        python_version="3.12",
        sdk_version="1.0",
        package_path="plugins/plugin_build_docker",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="image.tar.zst",
        runtime_fingerprint=fingerprint,
        build_metadata_json={
            "schema_version": "1.0",
            "build_id": "build-docker",
            "plugin_id": "nc_to_shp",
            "plugin_version": "1.0.0",
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
        runtime_type="docker",
        fingerprint=fingerprint,
        image_digest=fingerprint,
        metadata_json={"type": "docker", "digest": fingerprint},
        status="READY",
    )
    session.add_all([plugin, plugin_version, build, environment])
    session.commit()
