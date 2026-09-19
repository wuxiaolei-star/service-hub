"""Webhook delivery history persistence and listing endpoint tests (viewer-gated)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, JobCallback, PluginBuild, UserRecord, WebhookDelivery
from hub_server.routers.webhooks import router as webhooks_router
from hub_server.services import webhooks
from hub_server.services.auth import AuthService, hash_password
from hub_server.services.webhooks import deliver_due, enqueue_callback
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
from sqlalchemy.orm import Session

from tests.server.test_jobs_api import _seed_build

CALLBACK_URL = "https://hooks.example.test/done"


def _settings(tmp_path: Path, *, auth_mode: str = "off") -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode=auth_mode),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


@pytest.fixture
def environment(tmp_path: Path) -> Iterator[tuple[Session, TestClient]]:
    """A session and client sharing one database, without auth requirements."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client, app.state.session_factory() as session:
        yield session, client


def _api_client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path, auth_mode="required"))
    app.include_router(webhooks_router, prefix="/api/v1")
    return TestClient(app)


def _seed_job(session: Session, client: TestClient, *, status: str = "SUCCESS") -> Job:
    """Seed one enabled Build plus a terminal Job in the shared database."""
    _seed_build(client, runtime_type="docker")
    build = session.query(PluginBuild).filter_by(build_key="plugin_build_docker").one()
    job = Job(
        plugin_build=build,
        runtime_type="docker",
        runtime_fingerprint="x",
        status=status,
        params_json={},
        inputs_json={},
        timeout_seconds=60,
        cancel_requested=False,
    )
    session.add(job)
    session.commit()
    return job


def _token_for(client: TestClient, username: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _seed_job_api(client: TestClient, *, status: str = "SUCCESS") -> tuple[str, int]:
    """Seed one enabled Build plus a terminal Job; return (job_key, job_id)."""
    factory = client.app.state.session_factory
    with factory() as session:
        build = (
            session.query(PluginBuild).filter_by(build_key="plugin_build_docker").one_or_none()
        )
    if build is None:
        _seed_build(client, runtime_type="docker")
    with factory() as session:
        build = session.query(PluginBuild).filter_by(build_key="plugin_build_docker").one()
        job = Job(
            plugin_build=build,
            runtime_type="docker",
            runtime_fingerprint="x",
            status=status,
            params_json={},
            inputs_json={},
            timeout_seconds=60,
            cancel_requested=False,
        )
        session.add(job)
        session.commit()
        return job.job_key, job.id


def _seed_callback(client: TestClient, job_id: int) -> int:
    """Insert one PENDING callback; return its id."""
    factory = client.app.state.session_factory
    with factory() as session:
        callback = JobCallback(job_id=job_id, url=CALLBACK_URL, state="PENDING")
        session.add(callback)
        session.commit()
        return callback.id


def test_successful_delivery_records_ok_delivery(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 2xx response writes one ok=True WebhookDelivery with the status code."""
    session, client = environment
    job = _seed_job(session, client)
    callback = enqueue_callback(session, job.id, CALLBACK_URL)
    session.commit()
    monkeypatch.setattr(webhooks, "_post_json", lambda url, body, headers: 204)
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    assert deliver_due(session, now=now) == 1

    deliveries = session.query(WebhookDelivery).all()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery.callback_id == callback.id
    assert delivery.ok is True
    assert delivery.status_code == 204
    assert delivery.error is None
    assert delivery.attempted_at.replace(tzinfo=UTC) == now


def test_http_failure_records_failed_delivery(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-2xx response writes one ok=False WebhookDelivery with the status code."""
    session, client = environment
    job = _seed_job(session, client)
    callback = enqueue_callback(session, job.id, CALLBACK_URL)
    session.commit()
    monkeypatch.setattr(webhooks, "_post_json", lambda url, body, headers: 503)
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    assert deliver_due(session, now=now) == 1

    delivery = session.query(WebhookDelivery).one()
    assert delivery.callback_id == callback.id
    assert delivery.ok is False
    assert delivery.status_code == 503
    assert "HTTP 503" in (delivery.error or "")


def test_transport_failure_records_error_without_status(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport failure records the error message and no status code."""
    session, client = environment
    job = _seed_job(session, client)
    enqueue_callback(session, job.id, CALLBACK_URL)
    session.commit()

    def refused(url: str, body: bytes, headers: dict[str, str]) -> int:
        raise OSError("connection refused")

    monkeypatch.setattr(webhooks, "_post_json", refused)

    assert deliver_due(session, now=datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)) == 1

    delivery = session.query(WebhookDelivery).one()
    assert delivery.ok is False
    assert delivery.status_code is None
    assert "connection refused" in (delivery.error or "")


def test_delivery_history_lists_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every attempt appends one row and the endpoint returns newest first."""
    with _api_client(tmp_path) as client:
        headers = {"Authorization": f"Bearer {_token_for(client, 'admin')}"}
        job_key, job_id = _seed_job_api(client)
        callback_id = _seed_callback(client, job_id)
        responses = iter([503, 503, 200])
        monkeypatch.setattr(
            webhooks, "_post_json", lambda url, body, hdrs: next(responses)
        )
        start = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
        factory = client.app.state.session_factory
        with factory() as session:
            assert deliver_due(session, now=start) == 1
            assert deliver_due(session, now=start + timedelta(minutes=1)) == 1
            assert deliver_due(session, now=start + timedelta(minutes=5)) == 1

        response = client.get(
            f"/api/v1/jobs/{job_key}/callbacks/{callback_id}/deliveries", headers=headers
        )

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 3
        assert [item["ok"] for item in items] == [True, False, False]
        assert [item["status_code"] for item in items] == [200, 503, 503]
        assert "HTTP 503" in (items[1]["error"] or "")
        assert set(items[0]) == {"id", "attempted_at", "status_code", "ok", "error"}
        timestamps = [datetime.fromisoformat(item["attempted_at"]) for item in items]
        assert timestamps[0] > timestamps[1] > timestamps[2]
        assert timestamps[-1] == start


def test_deliveries_allow_viewer_and_reject_anonymous(tmp_path: Path) -> None:
    """Anonymous callers get 401 while any authenticated viewer may read history."""
    with _api_client(tmp_path) as client:
        job_key, job_id = _seed_job_api(client)
        callback_id = _seed_callback(client, job_id)
        factory = client.app.state.session_factory
        with factory() as session:
            session.add(
                WebhookDelivery(
                    callback_id=callback_id,
                    attempted_at=datetime.now(UTC),
                    status_code=200,
                    ok=True,
                    error=None,
                )
            )
            session.add(
                UserRecord(
                    username="watcher",
                    password_hash=hash_password("password-secret"),
                    role="viewer",
                )
            )
            session.commit()

        anonymous = client.get(
            f"/api/v1/jobs/{job_key}/callbacks/{callback_id}/deliveries"
        )
        assert anonymous.status_code == 401
        assert anonymous.json()["error"]["code"] == "AUTH_REQUIRED"

        viewer_headers = {"Authorization": f"Bearer {_token_for(client, 'watcher')}"}
        response = client.get(
            f"/api/v1/jobs/{job_key}/callbacks/{callback_id}/deliveries",
            headers=viewer_headers,
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1
        assert items[0]["ok"] is True
        assert items[0]["status_code"] == 200


def test_deliveries_unknown_job_returns_404(tmp_path: Path) -> None:
    with _api_client(tmp_path) as client:
        headers = {"Authorization": f"Bearer {_token_for(client, 'admin')}"}

        response = client.get(
            "/api/v1/jobs/job_unknown/callbacks/1/deliveries", headers=headers
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_deliveries_unknown_callback_returns_404(tmp_path: Path) -> None:
    with _api_client(tmp_path) as client:
        headers = {"Authorization": f"Bearer {_token_for(client, 'admin')}"}
        job_key, _job_id = _seed_job_api(client)

        response = client.get(
            f"/api/v1/jobs/{job_key}/callbacks/999999/deliveries", headers=headers
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CALLBACK_NOT_FOUND"


def test_deliveries_callback_of_another_job_returns_404(tmp_path: Path) -> None:
    """Delivery history is only reachable through the Job that owns the callback."""
    with _api_client(tmp_path) as client:
        headers = {"Authorization": f"Bearer {_token_for(client, 'admin')}"}
        _own_key, owner_job_id = _seed_job_api(client)
        callback_id = _seed_callback(client, owner_job_id)
        other_key, _other_job_id = _seed_job_api(client)

        response = client.get(
            f"/api/v1/jobs/{other_key}/callbacks/{callback_id}/deliveries",
            headers=headers,
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CALLBACK_NOT_FOUND"
