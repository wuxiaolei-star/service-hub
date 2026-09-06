"""Smoke coverage for the deployable Docker Compose service."""

import json
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest


def _compose_command(
    repository_root: Path,
    project_name: str,
    *arguments: str,
    compose_file: Path | None = None,
) -> list[str]:
    """Build a Compose command tied to this repository and an isolated project."""
    selected_compose_file = compose_file or repository_root / "compose.yaml"
    command = [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--project-directory",
        str(repository_root),
        "--file",
        str(selected_compose_file),
    ]
    return [*command, *arguments]


def _available_local_port() -> int:
    """Reserve an ephemeral loopback port number for a short-lived smoke deployment."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as socket_handle:
        socket_handle.bind(("127.0.0.1", 0))
        return int(socket_handle.getsockname()[1])


def _wait_for_health(url: str) -> httpx.Response:
    """Wait for Uvicorn startup before asserting the deployment health contract."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code == 200:
            return response
        time.sleep(0.2)
    raise AssertionError("Compose service did not become healthy within 30 seconds")


def _write_smoke_compose(
    path: Path, repository_root: Path, data_directory: Path, port: int
) -> None:
    """Write a standalone deployment that never merges with production port mappings."""
    config_file = repository_root / "config" / "hub.yaml"
    path.write_text(
        "services:\n"
        "  hub:\n"
        "    build:\n"
        f"      context: {json.dumps(str(repository_root))}\n"
        "      dockerfile: Dockerfile\n"
        "    image: python-service-hub:0.1.0-linux-amd64\n"
        "    platform: linux/amd64\n"
        "    ports:\n"
        f"      - {json.dumps(f'127.0.0.1:{port}:8000')}\n"
        "    volumes:\n"
        f"      - {json.dumps(f'{config_file}:/app/config/hub.yaml:ro')}\n"
        f"      - {json.dumps(f'{data_directory}:/data')}\n",
        encoding="utf-8",
    )


def test_compose_command_uses_an_explicit_project_and_file() -> None:
    """Smoke commands must not inherit a caller's active Compose project."""
    repository_root = Path("/workspace/python-service-hub")

    assert _compose_command(repository_root, "hub-smoke-123", "ps") == [
        "docker",
        "compose",
        "--project-name",
        "hub-smoke-123",
        "--project-directory",
        str(repository_root),
        "--file",
        str(repository_root / "compose.yaml"),
        "ps",
    ]


def test_compose_command_can_use_a_standalone_smoke_file() -> None:
    """The disposable deployment must not merge its mappings with production Compose."""
    repository_root = Path("/workspace/python-service-hub")
    smoke_compose = Path("/tmp/compose.smoke.yaml")

    assert _compose_command(
        repository_root,
        "hub-smoke-123",
        "ps",
        compose_file=smoke_compose,
    ) == [
        "docker",
        "compose",
        "--project-name",
        "hub-smoke-123",
        "--project-directory",
        str(repository_root),
        "--file",
        str(smoke_compose),
        "ps",
    ]


@pytest.mark.integration
def test_compose_health_and_upload() -> None:
    """A disposable AMD64 Compose project exposes health and accepts uploads."""
    repository_root = Path(__file__).resolve().parents[2]
    project_name = f"hub-smoke-{uuid.uuid4().hex}"
    port = _available_local_port()

    with tempfile.TemporaryDirectory(prefix="python-service-hub-smoke-") as temporary_directory:
        temporary_root = Path(temporary_directory).resolve()
        data_directory = temporary_root / "data"
        assert data_directory.parent == temporary_root
        data_directory.mkdir()
        smoke_compose_file = temporary_root / "compose.smoke.yaml"
        _write_smoke_compose(smoke_compose_file, repository_root, data_directory, port)
        command = _compose_command(
            repository_root,
            project_name,
            "up",
            "-d",
            "--build",
            compose_file=smoke_compose_file,
        )
        try:
            subprocess.run(command, check=True, cwd=repository_root)

            health = _wait_for_health(f"http://127.0.0.1:{port}/api/v1/system/health")
            assert health.json() == {"status": "UP"}

            response = httpx.post(
                f"http://127.0.0.1:{port}/api/v1/files",
                files={"file": ("smoke.nc", b"data")},
                timeout=30,
            )
            assert response.status_code == 201
        finally:
            subprocess.run(
                _compose_command(
                    repository_root,
                    project_name,
                    "down",
                    "--volumes",
                    "--remove-orphans",
                    compose_file=smoke_compose_file,
                ),
                check=False,
                cwd=repository_root,
            )
