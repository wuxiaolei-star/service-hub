"""SSE log stream contract tests for the logs-stream router."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion
from hub_server.routers.logs_stream import router as logs_stream_router
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

_STREAM_READ_TIMEOUT_SECONDS = 20.0
_MAX_STREAM_LINES = 400


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),
    )


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(logs_stream_router, prefix="/api/v1")
    return TestClient(app)


def _viewer_headers(client: TestClient) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = _first_or_create_viewer(session)
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _first_or_create_viewer(session: object) -> object:
    from hub_server.models import UserRecord

    existing = session.query(UserRecord).filter(UserRecord.username == "watcher").one_or_none()
    if existing is not None:
        return existing
    user = UserRecord(
        username="watcher",
        password_hash=hash_password("long-enough-pass"),
        role="viewer",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_job(
    client: TestClient,
    job_key: str,
    *,
    status: str,
    messages: list[str],
) -> None:
    """Create one Job with its build chain and a real events.jsonl below storage."""
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key=f"plugin_{job_key}", name="Stream Plugin")
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
            build_key=f"build_{job_key}",
            manifest_build_id=f"build-{job_key}",
            plugin_version=version,
            target_os="linux",
            target_arch="amd64",
            runtime_type="docker",
            python_version="3.12",
            sdk_version="1.0",
            package_path=f"plugins/build_{job_key}",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint="f" * 64,
            build_metadata_json={"schema_version": "1.0", "build_id": f"build-{job_key}"},
            status="ENABLED",
        )
        session.add(
            Job(
                job_key=job_key,
                plugin_build=build,
                runtime_type="docker",
                runtime_fingerprint="f" * 64,
                status=status,
                params_json={},
                inputs_json={},
                timeout_seconds=30,
                cancel_requested=False,
                workspace_path=f"jobs/{job_key}",
            )
        )
        session.commit()
    log_path = client.app.state.storage.open_relative(f"jobs/{job_key}/logs/events.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "".join(_event_line(message) for message in messages),
        encoding="utf-8",
    )


def _event_line(message: str) -> str:
    event = {"protocol_version": "1.0", "type": "log", "level": "INFO", "message": message}
    return json.dumps(event, ensure_ascii=False) + "\n"


def _complete_job_later(
    client: TestClient, job_key: str, *, delay_seconds: float
) -> threading.Timer:
    """Flip a RUNNING Job to SUCCESS while the stream is open, so the stream ends."""

    def flip() -> None:
        with client.app.state.session_factory() as session:
            job = session.query(Job).filter(Job.job_key == job_key).one()
            job.status = "SUCCESS"
            session.commit()

    timer = threading.Timer(delay_seconds, flip)
    timer.daemon = True
    return timer


def _collect_stream(
    client: TestClient,
    url: str,
    headers: dict[str, str],
    *,
    stop_after_log_events: int | None = None,
) -> list[str]:
    """Collect raw SSE lines in a daemon thread so a stalled stream cannot hang."""
    collected: list[str] = []
    failures: list[str] = []
    finished = threading.Event()

    def read() -> None:
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    failures.append(f"unexpected status {response.status_code}")
                    return
                completed_log_events = 0
                current_event: str | None = None
                for line in response.iter_lines():
                    collected.append(line)
                    if line.startswith("event:"):
                        current_event = line.removeprefix("event:").strip()
                    elif line.startswith("data:") and current_event is not None:
                        if current_event == "log":
                            completed_log_events += 1
                        current_event = None
                        if (
                            stop_after_log_events is not None
                            and completed_log_events >= stop_after_log_events
                        ):
                            break
                    if len(collected) >= _MAX_STREAM_LINES:
                        break
        except Exception as error:  # surfaced to the main thread below
            failures.append(f"{type(error).__name__}: {error}")
        finally:
            finished.set()

    reader = threading.Thread(target=read, daemon=True, name="sse-reader")
    reader.start()
    if not finished.wait(timeout=_STREAM_READ_TIMEOUT_SECONDS):
        pytest.fail(f"SSE stream did not finish within {_STREAM_READ_TIMEOUT_SECONDS}s")
    assert not failures, failures
    return collected


def _parse_sse(lines: list[str]) -> list[tuple[str, str]]:
    """Return (event, data) pairs from collected SSE lines."""
    events: list[tuple[str, str]] = []
    current: str | None = None
    for line in lines:
        if line.startswith("event:"):
            current = line.removeprefix("event:").strip()
        elif line.startswith("data:") and current is not None:
            events.append((current, line.removeprefix("data:").strip()))
            current = None
    return events


def _log_messages(events: list[tuple[str, str]]) -> list[str]:
    return [json.loads(data)["message"] for name, data in events if name == "log"]


def test_stream_requires_authentication(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_job(client, "job_stream_auth", status="SUCCESS", messages=[])
        response = client.get("/api/v1/jobs/job_stream_auth/logs/stream")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_stream_unknown_job_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _viewer_headers(client)
        response = client.get("/api/v1/jobs/job_missing/logs/stream", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_stream_running_job_delivers_log_events_before_disconnect(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        headers = _viewer_headers(client)
        _seed_job(
            client,
            "job_stream_live",
            status="RUNNING",
            messages=["m1", "m2", "m3"],
        )
        timer = _complete_job_later(client, "job_stream_live", delay_seconds=0.5)
        timer.start()
        lines = _collect_stream(
            client,
            "/api/v1/jobs/job_stream_live/logs/stream",
            headers,
            stop_after_log_events=3,
        )
        timer.join()

        events = _parse_sse(lines)
        names = [name for name, _data in events]
        assert names == ["log", "log", "log"]
        assert _log_messages(events) == ["m1", "m2", "m3"]


def test_stream_terminal_job_sends_end_after_logs(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _viewer_headers(client)
        _seed_job(
            client,
            "job_stream_done",
            status="SUCCESS",
            messages=["first", "second", "third"],
        )
        response = client.get("/api/v1/jobs/job_stream_done/logs/stream", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(response.text.splitlines())
        assert _log_messages(events) == ["first", "second", "third"]
        assert events[-1] == ("end", "{}")


def test_stream_data_lines_are_single_line_json(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _viewer_headers(client)
        messages = ["第一行\n第二行", 'quote " and \\ backslash', "unicode 测✓"]
        _seed_job(
            client,
            "job_stream_json",
            status="FAILED",
            messages=messages,
        )
        response = client.get("/api/v1/jobs/job_stream_json/logs/stream", headers=headers)
        assert response.status_code == 200

        data_lines = [
            line.removeprefix("data:").strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert len(data_lines) == 4  # three log events plus the end event
        for data_line in data_lines:
            assert "\n" not in data_line and "\r" not in data_line
            json.loads(data_line)  # every data line is one complete JSON document
        assert _log_messages(_parse_sse(response.text.splitlines())) == messages


def test_stream_viewer_token_allowed_and_anonymous_denied(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        _seed_job(client, "job_stream_roles", status="SUCCESS", messages=["hello"])
        anonymous = client.get("/api/v1/jobs/job_stream_roles/logs/stream")
        assert anonymous.status_code == 401

        headers = _viewer_headers(client)
        allowed = client.get("/api/v1/jobs/job_stream_roles/logs/stream", headers=headers)
        assert allowed.status_code == 200
        assert _log_messages(_parse_sse(allowed.text.splitlines())) == ["hello"]
