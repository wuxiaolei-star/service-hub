"""Metrics endpoint contract tests (admin only, Prometheus text format)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion, UserRecord
from hub_server.services import metrics as metrics_module
from hub_server.services.auth import AuthService
from hub_server.services.metrics import render_metrics
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


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


def _admin_headers(client: TestClient) -> dict[str, str]:
    metrics_module._clear_metrics_cache()
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_settings(tmp_path)))


def test_metrics_requires_admin(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        anonymous = client.get("/api/v1/system/metrics")
        assert anonymous.status_code == 401


def test_metrics_outputs_prometheus_text(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        response = client.get("/api/v1/system/metrics", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        for expected in (
            "# HELP hub_jobs_total",
            "# TYPE hub_jobs_total counter",
            'hub_jobs_total{plugin="none",runtime="none",status="none"} 0',
            "# HELP hub_job_duration_seconds",
            "# TYPE hub_job_duration_seconds histogram",
            'hub_job_duration_seconds_bucket{le="0.1"} 0',
            'hub_job_duration_seconds_bucket{le="+Inf"} 0',
            "hub_job_duration_seconds_sum 0",
            "hub_job_duration_seconds_count 0",
            "hub_jobs_active 0",
            "hub_files_total 0",
            "hub_files_bytes_total 0",
            "hub_users_total 1",
            "hub_uptime_seconds ",
        ):
            assert expected in body, f"missing {expected!r} in:\n{body}"


def test_metrics_reflect_created_users_and_files(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        client.post(
            "/api/v1/users",
            headers=headers,
            json={"username": "op", "password": "long-enough-pass", "role": "operator"},
        )
        body = client.get("/api/v1/system/metrics", headers=headers).text
        assert "hub_users_total 2" in body


def test_metrics_job_histogram_and_plugin_counter(tmp_path: Path) -> None:
    """G8: durations land in the right buckets and jobs count per plugin/runtime/status."""
    with _client(tmp_path) as client:
        factory = client.app.state.session_factory
        with factory() as session:
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
                build_key="plugin_build_metrics",
                manifest_build_id="build-metrics",
                plugin_version=version,
                target_os="linux",
                target_arch="amd64",
                runtime_type="docker",
                python_version="3.12",
                sdk_version="1.0",
                package_path="plugins/plugin_build_metrics",
                package_sha256="b" * 64,
                source_sha256="a" * 64,
                runtime_archive_path="runtime/env.tar.zst",
                runtime_fingerprint="d" * 64,
                build_metadata_json={"schema_version": "1.0"},
                status="ENABLED",
            )
            session.add_all([plugin, version, build])
            session.commit()
            build_id = build.id

        now = datetime.now(UTC)

        def _job(status: str, *, duration: timedelta | None) -> Job:
            started = now - timedelta(minutes=5)
            return Job(
                plugin_build_id=build_id,
                runtime_type="docker",
                runtime_fingerprint="d" * 64,
                status=status,
                params_json={},
                inputs_json={},
                timeout_seconds=60,
                created_at=started,
                started_at=started,
                finished_at=started + duration if duration is not None else None,
            )

        with factory() as session:
            session.add_all(
                [
                    _job("SUCCESS", duration=timedelta(seconds=0.05)),
                    _job("SUCCESS", duration=timedelta(seconds=2)),
                    _job("FAILED", duration=timedelta(seconds=30)),
                    # Never finished: contributes no duration sample.
                    _job("TIMED_OUT", duration=None),
                    # Never started: contributes no duration sample.
                    _job("PENDING", duration=None),
                ]
            )
            session.commit()

        metrics_module._clear_metrics_cache()
        with factory() as session:
            body = render_metrics(session, _settings(tmp_path))

        assert "# TYPE hub_jobs_total counter" in body
        assert 'hub_jobs_total{plugin="nc_to_shp",runtime="docker",status="SUCCESS"} 2' in body
        assert 'hub_jobs_total{plugin="nc_to_shp",runtime="docker",status="FAILED"} 1' in body
        assert 'hub_jobs_total{plugin="nc_to_shp",runtime="docker",status="TIMED_OUT"} 1' in body
        assert 'hub_jobs_total{plugin="nc_to_shp",runtime="docker",status="PENDING"} 1' in body

        assert "# TYPE hub_job_duration_seconds histogram" in body
        # Cumulative buckets: 0.05s <= 0.1; +2s <= 5; +30s <= 60. The two
        # timestamp-less jobs stay out of every bucket, sum, and count.
        assert 'hub_job_duration_seconds_bucket{le="0.1"} 1' in body
        assert 'hub_job_duration_seconds_bucket{le="0.5"} 1' in body
        assert 'hub_job_duration_seconds_bucket{le="1"} 1' in body
        assert 'hub_job_duration_seconds_bucket{le="5"} 2' in body
        assert 'hub_job_duration_seconds_bucket{le="15"} 2' in body
        assert 'hub_job_duration_seconds_bucket{le="60"} 3' in body
        assert 'hub_job_duration_seconds_bucket{le="300"} 3' in body
        assert 'hub_job_duration_seconds_bucket{le="1800"} 3' in body
        assert 'hub_job_duration_seconds_bucket{le="+Inf"} 3' in body
        assert "hub_job_duration_seconds_sum 32.05" in body
        assert "hub_job_duration_seconds_count 3" in body
