"""Webhook delivery state machine tests against the session-level service."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.errors import WebhookUrlForbiddenError
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Job, JobCallback, PluginBuild
from hub_server.services import webhooks
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

PostSpy = Callable[[str, bytes, dict[str, str]], int]


@pytest.fixture
def environment(tmp_path: Path) -> Iterator[tuple[Session, TestClient]]:
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
    with TestClient(app) as client, app.state.session_factory() as session:
        yield session, client


def _seed_job(session: Session, client: TestClient, *, status: str = "SUCCESS") -> Job:
    """Seed one enabled Build plus a Job in the given status."""
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


def _spy_post(
    calls: list[tuple[str, bytes, dict[str, str]]], *, status_code: int | None = 200
) -> PostSpy:
    """Build a _post_json replacement recording calls; None status raises OSError."""

    def fake_post(url: str, body: bytes, headers: dict[str, str]) -> int:
        calls.append((url, body, dict(headers)))
        if status_code is None:
            raise OSError("connection refused")
        return status_code

    return fake_post


def test_deliver_due_success_marks_succeeded(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, client = environment
    job = _seed_job(session, client)
    enqueue_callback(session, job.id, "https://hooks.example.test/done")
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=204))
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    processed = deliver_due(session, now=now)

    assert processed == 1
    callback = session.query(JobCallback).one()
    assert callback.state == "SUCCEEDED"
    assert callback.attempts == 0
    assert callback.last_status_code == 204
    assert callback.last_error is None
    assert len(calls) == 1
    url, body, _headers = calls[0]
    assert url == "https://hooks.example.test/done"
    assert json.loads(body) == {"job_id": job.job_key, "status": "SUCCESS", "error_summary": None}
    entry = session.query(AuditLogRecord).filter_by(action="webhook.deliver").one()
    assert entry.result == "ok"
    assert entry.actor_type == "system"
    assert entry.actor_name == "scheduler"
    assert entry.detail is not None
    assert entry.detail["url"] == "https://hooks.example.test/done"
    assert entry.detail["status_code"] == 204


def test_deliver_due_non_2xx_schedules_backoff(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, client = environment
    job = _seed_job(session, client)
    enqueue_callback(session, job.id, "https://hooks.example.test/done")
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=503))
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    assert deliver_due(session, now=now) == 1

    callback = session.query(JobCallback).one()
    assert callback.state == "FAILED"
    assert callback.attempts == 1
    assert callback.last_status_code == 503
    assert callback.next_attempt_at is not None
    assert callback.next_attempt_at.replace(tzinfo=UTC) == now + timedelta(seconds=60)
    entry = session.query(AuditLogRecord).filter_by(action="webhook.deliver").one()
    assert entry.result == "denied"

    # A retry that succeeds moves the callback back to SUCCEEDED.
    retry_calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(retry_calls, status_code=200))
    assert deliver_due(session, now=now + timedelta(minutes=5)) == 1
    recovered = session.query(JobCallback).one()
    assert recovered.state == "SUCCEEDED"
    assert len(retry_calls) == 1


def test_deliver_due_three_failures_exhausts(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, client = environment
    job = _seed_job(session, client)
    enqueue_callback(session, job.id, "https://hooks.example.test/done")
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=None))
    start = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    assert deliver_due(session, now=start) == 1
    assert deliver_due(session, now=start + timedelta(seconds=60)) == 1
    assert deliver_due(session, now=start + timedelta(seconds=180)) == 1

    callback = session.query(JobCallback).one()
    assert callback.state == "EXHAUSTED"
    assert callback.attempts == 3
    assert callback.last_status_code is None
    assert "connection refused" in (callback.last_error or "")
    assert len(calls) == 3
    entries = session.query(AuditLogRecord).filter_by(action="webhook.deliver").all()
    assert len(entries) == 3
    assert all(entry.result == "denied" for entry in entries)
    # Exhausted callbacks are never picked up again.
    assert deliver_due(session, now=start + timedelta(seconds=600)) == 0


def test_deliver_due_signs_body_with_secret(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, client = environment
    job = _seed_job(session, client)
    secret = "callback-secret"
    enqueue_callback(session, job.id, "https://hooks.example.test/done", secret=secret)
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=200))

    assert deliver_due(session, now=datetime.now(UTC)) == 1

    _url, body, headers = calls[0]
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert headers["X-Hub-Signature"] == f"sha256={expected}"


def test_deliver_due_skips_non_terminal_job(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    session, client = environment
    job = _seed_job(session, client, status="RUNNING")
    enqueue_callback(session, job.id, "https://hooks.example.test/done")
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=200))

    assert deliver_due(session, now=datetime.now(UTC)) == 0

    assert calls == []
    callback = session.query(JobCallback).one()
    assert callback.state == "PENDING"
    assert session.query(AuditLogRecord).count() == 0


@pytest.mark.parametrize("status", ["SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT"])
def test_deliver_due_covers_every_terminal_status(
    environment: tuple[Session, TestClient],
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    session, client = environment
    job = _seed_job(session, client, status=status)
    enqueue_callback(session, job.id, "https://hooks.example.test/done")
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=200))

    assert deliver_due(session, now=datetime.now(UTC)) == 1

    callback = session.query(JobCallback).one()
    assert callback.state == "SUCCEEDED"
    assert json.loads(calls[0][1])["status"] == status


def test_enqueue_callback_rejects_a_forbidden_target(
    environment: tuple[Session, TestClient],
) -> None:
    """A refused callback must not be persisted for the scheduler to dial later."""
    session, client = environment
    job = _seed_job(session, client)

    with pytest.raises(WebhookUrlForbiddenError):
        enqueue_callback(session, job.id, "http://169.254.169.254/latest/meta-data/")

    session.rollback()
    assert session.query(JobCallback).count() == 0


def test_deliver_due_never_dials_a_forbidden_target(
    environment: tuple[Session, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored callback that predates the guard is audited, never requested."""
    session, client = environment
    job = _seed_job(session, client)
    session.add(JobCallback(job_id=job.id, url="http://127.0.0.1:8001/health", state="PENDING"))
    session.commit()
    calls: list[tuple[str, bytes, dict[str, str]]] = []
    monkeypatch.setattr(webhooks, "_post_json", _spy_post(calls, status_code=200))
    start = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    assert deliver_due(session, now=start) == 1
    assert deliver_due(session, now=start + timedelta(seconds=60)) == 1
    assert deliver_due(session, now=start + timedelta(seconds=180)) == 1

    callback = session.query(JobCallback).one()
    assert callback.state == "EXHAUSTED"
    assert callback.attempts == 3
    assert callback.last_status_code is None
    assert "公网地址" in (callback.last_error or "")
    assert calls == []
    entries = session.query(AuditLogRecord).filter_by(action="webhook.deliver").all()
    assert len(entries) == 3
    assert all(entry.result == "denied" for entry in entries)
