"""Webhook callback replay endpoint and service tests (operator-gated)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.errors import HubError
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Job, JobCallback, PluginBuild, UserRecord
from hub_server.routers.webhooks import router as webhooks_router
from hub_server.services import webhooks
from hub_server.services.auth import AuthService, hash_password
from hub_server.services.webhooks import deliver_due, enqueue_callback, replay_callback
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


def _settings(tmp_path: Path, *, auth_mode: str = "required") -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode=auth_mode),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(webhooks_router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def service_environment(tmp_path: Path) -> Iterator[tuple[Session, TestClient]]:
    """A session and client sharing one database, without auth requirements."""
    app = create_app(_settings(tmp_path, auth_mode="off"))
    with TestClient(app) as client, app.state.session_factory() as session:
        yield session, client


def _admin_headers(client: TestClient) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _token_for(client: TestClient, username: str) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return token


def _seed_job(client: TestClient, *, status: str = "SUCCESS") -> tuple[str, int]:
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


def _seed_callback(
    client: TestClient,
    job_id: int,
    *,
    state: str,
    attempts: int = 0,
    last_status_code: int | None = None,
    last_error: str | None = None,
    next_attempt_at: datetime | None = None,
) -> int:
    """Insert one JobCallback in the given state; return its id."""
    factory = client.app.state.session_factory
    with factory() as session:
        callback = JobCallback(
            job_id=job_id,
            url=CALLBACK_URL,
            state=state,
            attempts=attempts,
            last_status_code=last_status_code,
            last_error=last_error,
            next_attempt_at=next_attempt_at,
        )
        session.add(callback)
        session.commit()
        return callback.id


def _replay(
    client: TestClient, job_key: str, callback_id: int, headers: dict[str, str]
) -> object:
    return client.post(
        f"/api/v1/jobs/{job_key}/callbacks/{callback_id}/replay", headers=headers
    )


def test_replay_exhausted_callback_resets_and_redelivers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXHAUSTED callback becomes PENDING again and is delivered once more."""
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        stale = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
        callback_id = _seed_callback(
            client,
            job_id,
            state="EXHAUSTED",
            attempts=3,
            last_error="connection refused",
            next_attempt_at=stale,
        )

        response = _replay(client, job_key, callback_id, headers)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "id": callback_id,
            "url": CALLBACK_URL,
            "state": "PENDING",
            "attempts": 0,
        }
        factory = client.app.state.session_factory
        with factory() as session:
            callback = session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.state == "PENDING"
            assert callback.attempts == 0
            assert callback.next_attempt_at is None
            assert callback.last_status_code is None
            assert callback.last_error is None

        # The replayed callback is picked up by the very next due scan.
        calls: list[tuple[str, bytes, dict[str, str]]] = []

        def fake_post(url: str, body: bytes, post_headers: dict[str, str]) -> int:
            calls.append((url, body, dict(post_headers)))
            return 200

        monkeypatch.setattr(webhooks, "_post_json", fake_post)
        with factory() as session:
            assert deliver_due(session, now=stale + timedelta(minutes=1)) == 1
        with factory() as session:
            redelivered = session.get(JobCallback, callback_id)
            assert redelivered is not None
            assert redelivered.state == "SUCCEEDED"
            assert len(calls) == 1


def test_replay_failed_callback_resets_state(tmp_path: Path) -> None:
    """A FAILED callback mid-backoff is reset to an immediate fresh retry."""
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(
            client,
            job_id,
            state="FAILED",
            attempts=1,
            last_status_code=503,
            last_error="HTTP 503",
            next_attempt_at=datetime.now(UTC) + timedelta(seconds=60),
        )

        response = _replay(client, job_key, callback_id, headers)

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "PENDING"
        assert response.json()["attempts"] == 0
        factory = client.app.state.session_factory
        with factory() as session:
            callback = session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.state == "PENDING"
            assert callback.attempts == 0
            assert callback.next_attempt_at is None
            assert callback.last_status_code is None
            assert callback.last_error is None


@pytest.mark.parametrize("state", ["SUCCEEDED", "PENDING"])
def test_replay_rejects_in_flight_or_delivered_callback(
    tmp_path: Path, state: str
) -> None:
    """PENDING and SUCCEEDED callbacks have nothing left to replay."""
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(client, job_id, state=state)

        response = _replay(client, job_key, callback_id, headers)

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "CALLBACK_NOT_REPLAYABLE"
        factory = client.app.state.session_factory
        with factory() as session:
            callback = session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.state == state
            assert callback.attempts == 0


def test_replay_unknown_job_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)

        response = client.post("/api/v1/jobs/job_unknown/callbacks/1/replay", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_replay_unknown_callback_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, _job_id = _seed_job(client)

        response = _replay(client, job_key, 999999, headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CALLBACK_NOT_FOUND"


def test_replay_callback_of_another_job_returns_404(tmp_path: Path) -> None:
    """A callback is only replayable through the Job that owns it."""
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        _own_key, owner_job_id = _seed_job(client)
        callback_id = _seed_callback(client, owner_job_id, state="EXHAUSTED", attempts=3)
        other_key, _other_job_id = _seed_job(client)

        response = _replay(client, other_key, callback_id, headers)

        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "CALLBACK_NOT_FOUND"
        factory = client.app.state.session_factory
        with factory() as session:
            callback = session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.state == "EXHAUSTED"
            assert callback.attempts == 3


def test_replay_requires_operator_role(tmp_path: Path) -> None:
    """A viewer may list callbacks but may not replay them."""
    with _client(tmp_path) as client:
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(client, job_id, state="EXHAUSTED")
        with client.app.state.session_factory() as session:
            session.add(
                UserRecord(
                    username="watcher",
                    password_hash=hash_password("password-secret"),
                    role="viewer",
                )
            )
            session.commit()
        headers = {"Authorization": f"Bearer {_token_for(client, 'watcher')}"}

        response = _replay(client, job_key, callback_id, headers)

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "FORBIDDEN"


def test_replay_requires_authentication(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(client, job_id, state="EXHAUSTED")

        response = _replay(client, job_key, callback_id, headers={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_replay_writes_audit_entry(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(client, job_id, state="EXHAUSTED", attempts=3)

        response = _replay(client, job_key, callback_id, headers)

        assert response.status_code == 200, response.text
        factory = client.app.state.session_factory
        with factory() as session:
            entry = session.query(AuditLogRecord).filter_by(action="webhook.replay").one()
            assert entry.actor_type == "user"
            assert entry.actor_name == "admin"
            assert entry.resource_type == "job_callback"
            assert entry.resource_id == str(callback_id)
            assert entry.ip == "testclient"
            assert entry.result == "ok"


def test_replay_twice_returns_409_once_pending(tmp_path: Path) -> None:
    """A replayed callback is PENDING again, so a second replay is rejected."""
    with _client(tmp_path) as client:
        headers = _admin_headers(client)
        job_key, job_id = _seed_job(client)
        callback_id = _seed_callback(client, job_id, state="EXHAUSTED", attempts=3)

        first = _replay(client, job_key, callback_id, headers)
        second = _replay(client, job_key, callback_id, headers)

        assert first.status_code == 200, first.text
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "CALLBACK_NOT_REPLAYABLE"
        factory = client.app.state.session_factory
        with factory() as session:
            callback = session.get(JobCallback, callback_id)
            assert callback is not None
            assert callback.state == "PENDING"
            assert callback.attempts == 0


def test_replay_callback_service_rejects_missing_callback(
    service_environment: tuple[Session, TestClient],
) -> None:
    """The service layer raises CALLBACK_NOT_FOUND for an unknown id."""
    session, _client = service_environment

    with pytest.raises(HubError) as excinfo:
        replay_callback(session, 4242)

    assert excinfo.value.code == "CALLBACK_NOT_FOUND"
    assert excinfo.value.status_code == 404


def test_replay_callback_service_rejects_pending_callback(
    service_environment: tuple[Session, TestClient],
) -> None:
    session, client = service_environment
    _job_key, job_id = _seed_job(client)
    callback = enqueue_callback(session, job_id, CALLBACK_URL)
    session.commit()

    with pytest.raises(HubError) as excinfo:
        replay_callback(session, callback.id)

    assert excinfo.value.code == "CALLBACK_NOT_REPLAYABLE"
    assert excinfo.value.status_code == 409


def test_replay_callback_service_resets_exhausted_callback(
    service_environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The service reset alone lets the next deliver_due scan deliver again."""
    session, client = service_environment
    _job_key, job_id = _seed_job(client)
    callback = JobCallback(
        job_id=job_id,
        url=CALLBACK_URL,
        state="EXHAUSTED",
        attempts=3,
        last_error="connection refused",
        next_attempt_at=datetime.now(UTC),
    )
    session.add(callback)
    session.commit()

    replayed = replay_callback(session, callback.id)
    session.commit()

    assert replayed.state == "PENDING"
    assert replayed.attempts == 0
    assert replayed.next_attempt_at is None
    assert replayed.last_status_code is None
    assert replayed.last_error is None

    calls: list[tuple[str, bytes, dict[str, str]]] = []

    def fake_post(url: str, body: bytes, post_headers: dict[str, str]) -> int:
        calls.append((url, body, dict(post_headers)))
        return 200

    monkeypatch.setattr(webhooks, "_post_json", fake_post)
    assert deliver_due(session, now=datetime.now(UTC)) == 1
    reloaded = session.query(JobCallback).one()
    assert reloaded.state == "SUCCEEDED"
    assert len(calls) == 1
