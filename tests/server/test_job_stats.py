"""Job duration statistics endpoint tests (optimization plan 4.2 data source)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion, UserRecord
from hub_server.services.auth import AuthService, hash_password
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _settings(tmp_path: Path, mode: str) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode=mode),  # type: ignore[arg-type]
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Serve the API in off mode so data-focused tests need no credentials."""
    with TestClient(create_app(_settings(tmp_path, "off"))) as test_client:
        yield test_client


@pytest.fixture
def auth_client(tmp_path: Path) -> Iterator[TestClient]:
    """Serve the API in required mode with one viewer user seeded."""
    test_client = TestClient(create_app(_settings(tmp_path, "required")))
    with test_client:
        factory = test_client.app.state.session_factory
        with factory() as session:
            session.add(
                UserRecord(
                    username="u_viewer",
                    password_hash=hash_password("password-secret"),
                    role="viewer",
                )
            )
            session.commit()
        yield test_client


def _seed_build(client: TestClient) -> int:
    """Seed a minimal enabled docker Build and return its primary key."""
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"spec_version": "1.0"},
            status="INSTALLED",
        )
        build = PluginBuild(
            build_key="plugin_build_stats",
            manifest_build_id="build-stats",
            plugin_version=version,
            target_os="linux",
            target_arch="amd64",
            runtime_type="docker",
            python_version="3.12",
            sdk_version="1.0",
            package_path="plugins/plugin_build_stats",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint="d" * 64,
            build_metadata_json={"schema_version": "1.0"},
            status="ENABLED",
        )
        session.add_all([plugin, version, build])
        session.commit()
        return build.id


def _at(day: date, hour: int) -> datetime:
    """Build a UTC-aware timestamp for a specific calendar day and hour."""
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=UTC)


def _insert_job(
    client: TestClient,
    build_id: int,
    *,
    created_at: datetime,
    status: str = "PENDING",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            Job(
                plugin_build_id=build_id,
                runtime_type="docker",
                runtime_fingerprint="d" * 64,
                status=status,
                params_json={},
                inputs_json={},
                timeout_seconds=30,
                created_at=created_at,
                started_at=started_at,
                finished_at=finished_at,
            )
        )
        session.commit()


def test_stats_empty_database_returns_zeroed_buckets(client: TestClient) -> None:
    response = client.get("/api/v1/jobs/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 14
    assert len(body["buckets"]) == 14
    today = datetime.now(UTC).date()
    expected_dates = [(today - timedelta(days=13 - i)).isoformat() for i in range(14)]
    assert [bucket["date"] for bucket in body["buckets"]] == expected_dates
    for bucket in body["buckets"]:
        assert bucket["count"] == 0
        assert bucket["success_count"] == 0
        assert bucket["p50_ms"] is None
        assert bucket["p95_ms"] is None


def test_stats_aggregates_counts_and_percentiles(client: TestClient) -> None:
    build_id = _seed_build(client)
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    # Two completed SUCCESS jobs with known durations (1000ms and 3000ms).
    _insert_job(
        client,
        build_id,
        created_at=_at(yesterday, 10),
        status="SUCCESS",
        started_at=_at(yesterday, 10),
        finished_at=_at(yesterday, 10) + timedelta(seconds=1),
    )
    _insert_job(
        client,
        build_id,
        created_at=_at(yesterday, 11),
        status="SUCCESS",
        started_at=_at(yesterday, 11),
        finished_at=_at(yesterday, 11) + timedelta(seconds=3),
    )
    # A SUCCESS job without a finished_at contributes no duration sample.
    _insert_job(
        client,
        build_id,
        created_at=_at(yesterday, 12),
        status="SUCCESS",
        started_at=_at(yesterday, 12),
    )
    # A FAILED job inflates count but not success_count.
    _insert_job(client, build_id, created_at=_at(yesterday, 13), status="FAILED")
    # A PENDING job today keeps its own bucket.
    _insert_job(client, build_id, created_at=_at(today, 9), status="PENDING")

    response = client.get("/api/v1/jobs/stats", params={"days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 7
    assert len(body["buckets"]) == 7
    by_date = {bucket["date"]: bucket for bucket in body["buckets"]}

    yesterday_bucket = by_date[yesterday.isoformat()]
    assert yesterday_bucket["count"] == 4
    assert yesterday_bucket["success_count"] == 3
    # P50 of [1000, 3000] is 2000; the inclusive 95th percentile is 2900.
    assert yesterday_bucket["p50_ms"] == 2000
    assert yesterday_bucket["p95_ms"] == 2900

    today_bucket = by_date[today.isoformat()]
    assert today_bucket["count"] == 1
    assert today_bucket["success_count"] == 0
    assert today_bucket["p50_ms"] is None
    assert today_bucket["p95_ms"] is None


def test_stats_days_one_includes_only_today(client: TestClient) -> None:
    build_id = _seed_build(client)
    today = datetime.now(UTC).date()
    _insert_job(client, build_id, created_at=_at(today, 9), status="PENDING")

    response = client.get("/api/v1/jobs/stats", params={"days": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["days"] == 1
    assert [bucket["date"] for bucket in body["buckets"]] == [today.isoformat()]
    assert body["buckets"][0]["count"] == 1


@pytest.mark.parametrize("days", [0, 91, -1])
def test_stats_rejects_out_of_range_days(client: TestClient, days: int) -> None:
    response = client.get("/api/v1/jobs/stats", params={"days": days})

    assert response.status_code == 422


def test_stats_requires_viewer_role(auth_client: TestClient) -> None:
    token = _token_for(auth_client)
    authorized = auth_client.get(
        "/api/v1/jobs/stats", headers={"Authorization": f"Bearer {token}"}
    )
    assert authorized.status_code == 200

    anonymous = auth_client.get("/api/v1/jobs/stats")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"


def _token_for(client: TestClient) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "u_viewer").one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return token
