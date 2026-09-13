"""Single-container lifecycle and privilege-boundary acceptance on Linux AMD64."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

_DOCKER_PING_SNIPPET = (
    "import docker; client = docker.from_env(); client.ping(); client.close()"
)
_SUPERVISOR_STATES_SNIPPET = (
    "import json, sys;"
    "sys.path.insert(0, '/app');"
    "from deploy.service_hub.healthcheck import _supervisor_process_states;"
    "print(json.dumps(_supervisor_process_states()))"
)


@pytest.mark.integration
def test_single_container_survives_restart_and_keeps_privilege_boundaries(
    tmp_path: Path,
) -> None:
    """Restart persistence and socket boundaries are the release gate for the container."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("single-container lifecycle requires a native Linux AMD64 Docker host")

    _assert_single_container_deployment()

    payload = tmp_path / "lifecycle-input.txt"
    payload.write_bytes(b"single-container lifecycle check\n")
    base_url = os.environ.get("HUB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        with payload.open("rb") as handle:
            uploaded = client.post(
                "/api/v1/files",
                files={"file": (payload.name, handle, "text/plain")},
            )
        uploaded.raise_for_status()
        file_id = str(uploaded.json()["file_id"])

        _compose("restart", "service-hub")
        _wait_for_container_health()

        metadata = client.get(f"/api/v1/files/{file_id}")
        assert metadata.status_code == 200, "uploaded file metadata lost after restart"
        assert metadata.json()["file_id"] == file_id

    states = _supervisor_process_states()
    assert states == {
        "hub-api": "RUNNING",
        "conda-runner": "RUNNING",
        "docker-runner": "RUNNING",
    }

    for user in ("hub-api", "conda-runner"):
        denied = _docker_ping_as(user)
        assert denied.returncode != 0, f"{user} must not reach the Docker socket"
        combined = (denied.stdout + denied.stderr).lower()
        assert "permission denied" in combined, f"{user} failed for another reason: {combined}"

    allowed = _docker_ping_as("docker-runner")
    assert allowed.returncode == 0, (
        f"docker-runner must reach the Docker socket: {allowed.stdout} {allowed.stderr}"
    )


def _compose(*arguments: str) -> str:
    """Run a Compose command against the repository deployment and return stdout."""
    environment = dict(os.environ)
    environment.setdefault("HUB_HOST_DATA_DIR", "/srv/service-hub-data")
    result = subprocess.run(
        ["docker", "compose", "--project-directory", str(REPOSITORY_ROOT), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _container_health_state() -> str:
    container_id = _compose("ps", "-q", "service-hub").strip()
    assert container_id, "service-hub container is not running"
    inspect = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
        capture_output=True,
        text=True,
        check=True,
    )
    return inspect.stdout.strip()


def _wait_for_container_health(timeout_seconds: float = 120.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if _container_health_state() == "healthy":
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(2)
    raise AssertionError("service-hub container did not become healthy")


def _assert_single_container_deployment() -> None:
    """Preflight: exactly one long-running Compose service and a healthy container."""
    services = {
        line.strip()
        for line in _compose("config", "--services").splitlines()
        if line.strip()
    }
    assert services == {"service-hub"}, f"expected only service-hub: {sorted(services)}"
    _wait_for_container_health()


def _supervisor_process_states() -> dict[str, str]:
    """Read supervisord process states over the in-container Unix socket."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(REPOSITORY_ROOT),
            "exec",
            "-T",
            "-u",
            "root",
            "-w",
            "/app",
            "service-hub",
            "python",
            "-c",
            _SUPERVISOR_STATES_SNIPPET,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(result.stdout))


def _docker_ping_as(user: str) -> subprocess.CompletedProcess[str]:
    """Attempt a Docker Engine ping as one of the managed in-container users."""
    return subprocess.run(
        [
            "docker",
            "compose",
            "--project-directory",
            str(REPOSITORY_ROOT),
            "exec",
            "-T",
            "-u",
            user,
            "service-hub",
            "python",
            "-c",
            _DOCKER_PING_SNIPPET,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
