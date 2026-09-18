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
    username = os.environ.get("HUB_BOOTSTRAP_USERNAME", "admin")
    password = os.environ.get("HUB_BOOTSTRAP_PASSWORD")
    if not password:
        pytest.skip("set HUB_BOOTSTRAP_PASSWORD to exercise the lifecycle suite")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        with payload.open("rb") as handle:
            uploaded = client.post(
                "/api/v1/files",
                headers=headers,
                files={"file": (payload.name, handle, "text/plain")},
            )
        uploaded.raise_for_status()
        file_id = str(uploaded.json()["file_id"])

        _compose("restart", "service-hub")
        _wait_for_container_health()

        metadata = client.get(f"/api/v1/files/{file_id}", headers=headers)
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
    """Run a Compose command against the repository deployment and return stdout.

    A real deployment does not invoke ``docker compose`` bare: it pins a project name, layers
    the host override file over ``compose.yaml``, and exports the data-directory variable the
    stack interpolates. Those specifics are supplied through ``HUB_COMPOSE_COMMAND`` (the
    deploy script's own command line, split on whitespace) and ``HUB_HOST_DATA_DIR``, so the
    suite follows the deployment instead of guessing its flags.
    """
    environment = dict(os.environ)
    configured = environment.get("HUB_COMPOSE_COMMAND", "").split()
    if configured:
        command = [*configured, *arguments]
    else:
        environment.setdefault("HUB_HOST_DATA_DIR", "/srv/service-hub-data")
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(REPOSITORY_ROOT),
            *arguments,
        ]
    result = subprocess.run(
        command,
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
    """Preflight: the backend is deployed alongside its companions and is healthy.

    The stack always contains ``service-hub`` (the multi-process backend) and
    ``service-hub-web`` (Nginx + SPA); a real deployment adds ``service-hub-edge`` for TLS
    termination. The privilege boundary this suite guards is about the backend container, so
    required services are asserted as a subset and backend health is then verified.
    """
    services = {
        line.strip()
        for line in _compose("config", "--services").splitlines()
        if line.strip()
    }
    required = {"service-hub", "service-hub-web"}
    assert required <= services, f"missing services: {sorted(required - services)}"
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
