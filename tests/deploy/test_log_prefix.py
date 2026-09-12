from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("service", ["hub-api", "conda-runner", "docker-runner"])
def test_log_prefix_forwards_each_service_output_and_exit_status(service: str) -> None:
    """Removing a service wrapper would expose unprefixed container log lines."""
    command = [
        sys.executable,
        "-m",
        "deploy.service_hub.log_prefix",
        service,
        "--",
        sys.executable,
        "-c",
        "import sys; print('stdout', flush=True); print('stderr', file=sys.stderr, flush=True)",
    ]

    result = subprocess.run(command, capture_output=True, check=False, text=True)

    assert result.returncode == 0
    assert result.stdout.splitlines() == [f"[{service}] stdout", f"[{service}] stderr"]
    assert result.stderr == ""


def test_log_prefix_returns_the_wrapped_process_exit_code() -> None:
    """The wrapper must not hide a managed process failure from supervisord."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "deploy.service_hub.log_prefix",
            "hub-api",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(17)",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 17


def test_supervisor_wraps_all_three_service_log_streams() -> None:
    """Every persistent process must select a distinct container log prefix."""
    config = Path("deploy/service_hub/supervisord.conf").read_text(encoding="utf-8")

    assert "log_prefix hub-api -- uvicorn" in config
    assert "log_prefix conda-runner -- python -m deploy.runner.conda_runner_service" in config
    assert "log_prefix docker-runner -- python -m deploy.runner.docker_runner_service" in config
