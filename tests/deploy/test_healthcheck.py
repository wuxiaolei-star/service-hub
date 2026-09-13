from __future__ import annotations

import pytest

from deploy.service_hub import healthcheck
from deploy.service_hub.healthcheck import HealthcheckError, check_health


def test_health_requires_all_processes_running() -> None:
    """A non-running managed process makes the container unhealthy."""
    with pytest.raises(HealthcheckError, match="conda-runner"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "FATAL",
                "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
            },
            api_up=True,
            data_writable=True,
            docker_up=True,
        )


def test_health_requires_api() -> None:
    """A healthy process supervisor cannot hide an unavailable Hub API."""
    with pytest.raises(HealthcheckError, match="API"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "RUNNING",
                "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
            },
            api_up=False,
            data_writable=True,
            docker_up=True,
        )


def test_health_requires_writable_data() -> None:
    """The data volume must remain writable for jobs and persistence."""
    with pytest.raises(HealthcheckError, match="/data"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "RUNNING",
                "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
            },
            api_up=True,
            data_writable=False,
            docker_up=True,
        )


def test_health_requires_docker_ping() -> None:
    """Docker jobs require access to the host Docker Engine."""
    with pytest.raises(HealthcheckError, match="Docker"):
        check_health(
            process_states={
                "hub-api": "RUNNING",
                "conda-runner": "RUNNING",
                "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
            },
            api_up=True,
            data_writable=True,
            docker_up=False,
        )


def test_health_accepts_all_required_dependencies() -> None:
    """The decision seam accepts the complete healthy state."""
    check_health(
        process_states={
            "hub-api": "RUNNING",
            "conda-runner": "RUNNING",
            "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
        },
        api_up=True,
        data_writable=True,
        docker_up=True,
    )


def test_main_returns_success_when_runtime_probes_are_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The executable healthcheck combines all probes through the decision seam."""
    monkeypatch.setattr(
        healthcheck,
        "_supervisor_process_states",
        lambda: {
            "hub-api": "RUNNING",
            "conda-runner": "RUNNING",
            "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
        },
    )
    monkeypatch.setattr(healthcheck, "_api_is_up", lambda: True)
    monkeypatch.setattr(healthcheck, "_data_is_writable", lambda: True)
    monkeypatch.setattr(healthcheck, "_docker_is_up", lambda: True)

    assert healthcheck.main() == 0


def test_main_reports_a_single_safe_failure_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed probe must not expose environment values such as the runner token."""
    monkeypatch.setattr(
        healthcheck,
        "_supervisor_process_states",
        lambda: {
            "hub-api": "RUNNING",
            "conda-runner": "RUNNING",
            "docker-runner": "RUNNING",
        "cleaner": "RUNNING",
        },
    )
    monkeypatch.setattr(healthcheck, "_api_is_up", lambda: False)
    monkeypatch.setattr(healthcheck, "_data_is_writable", lambda: True)
    monkeypatch.setattr(healthcheck, "_docker_is_up", lambda: True)
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "must-not-appear")

    assert healthcheck.main() == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert "Hub API" in captured.err
    assert "must-not-appear" not in captured.err
