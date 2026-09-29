from __future__ import annotations

from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from deploy.runner.service_common import post_json, retry_startup


def test_retry_startup_retries_connection_errors() -> None:
    """A Hub that is still starting must not make a runner exit permanently."""
    attempts = 0
    delays: list[float] = []

    def action() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise URLError("not ready")

    retry_startup(action, sleep=delays.append, delay_seconds=0.25)

    assert attempts == 3
    assert delays == [0.25, 0.25]


def test_retry_startup_propagates_http_errors_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected startup reconciliation is a protocol error, not transient Hub latency."""
    error = HTTPError("http://hub/internal/v1/jobs/reconcile", 400, "bad request", {}, None)
    delays: list[float] = []

    def reject(_: Any, *, timeout: int) -> None:
        assert timeout == 30
        raise error

    monkeypatch.setattr("urllib.request.urlopen", reject)

    def unexpected_sleep(delay: float) -> None:
        delays.append(delay)
        raise AssertionError("HTTP errors must not be retried")

    with pytest.raises(HTTPError) as raised:
        retry_startup(
            lambda: post_json(
                "http://hub/internal/v1",
                "token",
                "/jobs/reconcile",
                {"runtime_type": "docker"},
            ),
            sleep=unexpected_sleep,
            delay_seconds=0.25,
        )

    assert raised.value is error
    assert delays == []


def test_post_json_preserves_http_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treating a protocol rejection as startup latency would hide invalid runner requests."""
    error = HTTPError("http://hub/internal/v1/jobs/claim", 400, "bad request", {}, None)

    def reject(_: Any, *, timeout: int) -> None:
        assert timeout == 30
        raise error

    monkeypatch.setattr("urllib.request.urlopen", reject)

    with pytest.raises(HTTPError) as raised:
        post_json("http://hub/internal/v1", "token", "/jobs/claim", {"runtime_type": "docker"})

    assert raised.value is error


def test_post_json_with_retry_retries_http_errors_with_backoff() -> None:
    """A transient 5xx on the terminal completion must not kill the main loop (M-3)."""
    from deploy.runner.service_common import post_json_with_retry

    error = HTTPError("http://hub/internal/v1/jobs/job_1/complete", 503, "unavailable", {}, None)
    attempts: list[str] = []
    delays: list[float] = []

    def flaky(_: str, __: str, path: str, ___: dict[str, Any]) -> dict[str, Any] | None:
        attempts.append(path)
        if len(attempts) < 3:
            raise error
        return {"id": "job_1", "status": "FAILED"}

    result = post_json_with_retry(
        "http://hub/internal/v1",
        "token",
        "/jobs/job_1/complete",
        {"runtime_type": "docker"},
        post=flaky,
        sleep=delays.append,
        backoff_seconds=0.5,
    )

    assert result == {"id": "job_1", "status": "FAILED"}
    assert len(attempts) == 3
    assert delays == [0.5, 1.0]


def test_post_json_with_retry_gives_up_after_final_attempt() -> None:
    """After the last attempt the failure is logged and the caller continues."""
    from deploy.runner.service_common import post_json_with_retry

    error = HTTPError("http://hub/internal/v1/jobs/job_1/complete", 500, "boom", {}, None)
    attempts: list[str] = []
    delays: list[float] = []

    def reject(_: str, __: str, path: str, ___: dict[str, Any]) -> dict[str, Any] | None:
        attempts.append(path)
        raise error

    result = post_json_with_retry(
        "http://hub/internal/v1",
        "token",
        "/jobs/job_1/complete",
        {"runtime_type": "docker"},
        post=reject,
        sleep=delays.append,
    )

    assert result is None
    assert len(attempts) == 3
    assert delays == [1.0, 2.0]
