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
