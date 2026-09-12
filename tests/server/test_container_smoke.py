"""Smoke coverage for the deployable Docker Compose service."""

import json
import platform
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml


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
    host_data_directory = data_directory.resolve()
    path.write_text(
        "services:\n"
        "  service-hub:\n"
        "    build:\n"
        f"      context: {json.dumps(str(repository_root))}\n"
        "      dockerfile: Dockerfile\n"
        "    image: python-service-hub:1.0.0-linux-amd64\n"
        "    platform: linux/amd64\n"
        "    environment:\n"
        f"      HUB_HOST_DATA_DIR: {json.dumps(str(host_data_directory))}\n"
        "    ports:\n"
        f"      - {json.dumps(f'127.0.0.1:{port}:8000')}\n"
        "    volumes:\n"
        f"      - {json.dumps(f'{host_data_directory}:/data')}\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n",
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


def test_production_compose_has_one_long_running_service() -> None:
    """Production Compose deploys the unified image through one service only."""
    model = yaml.safe_load(Path("compose.yaml").read_text("utf-8"))

    assert set(model["services"]) == {"service-hub"}
    service = model["services"]["service-hub"]
    required_data_directory = "${HUB_HOST_DATA_DIR:?HUB_HOST_DATA_DIR must be set}"
    assert service["image"] == "python-service-hub:1.0.0-linux-amd64"
    assert service["platform"] == "linux/amd64"
    assert service["restart"] == "unless-stopped"
    assert service["ports"] == ["127.0.0.1:8000:8000"]
    assert service["environment"] == {"HUB_HOST_DATA_DIR": required_data_directory}
    assert service["volumes"] == [
        f"{required_data_directory}:/data",
        "/var/run/docker.sock:/var/run/docker.sock",
    ]


def test_service_hub_and_docker_plugin_share_a_fixed_non_root_data_uid() -> None:
    """A plugin can write its Job output without opening the directory to every UID."""
    hub_dockerfile = Path("Dockerfile").read_text("utf-8")
    plugin_dockerfile = Path("packages/nc-to-shp-plugin/docker/Dockerfile").read_text(
        "utf-8"
    )

    assert "groupadd --system --gid 65532 hub-data" in hub_dockerfile
    assert "useradd --system --gid hub-data" in hub_dockerfile
    assert "USER 65532:65532" in plugin_dockerfile


def test_disposable_smoke_compose_uses_the_single_service_topology(tmp_path: Path) -> None:
    """The release smoke stack mirrors the one-service production topology."""
    repository_root = Path("/workspace/python-service-hub")
    compose_file = tmp_path / "compose.smoke.yaml"

    _write_smoke_compose(compose_file, repository_root, tmp_path / "data", 18080)

    model = yaml.safe_load(compose_file.read_text("utf-8"))
    assert set(model["services"]) == {"service-hub"}
    service = model["services"]["service-hub"]
    expected_host_root = str((tmp_path / "data").resolve())
    assert service["ports"] == ["127.0.0.1:18080:8000"]
    assert service["environment"] == {"HUB_HOST_DATA_DIR": expected_host_root}
    assert service["volumes"] == [
        f"{expected_host_root}:/data",
        "/var/run/docker.sock:/var/run/docker.sock",
    ]


@pytest.mark.integration
def test_compose_health_and_upload() -> None:
    """A disposable AMD64 Compose project exposes health and accepts uploads."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("Compose smoke requires a native Linux AMD64 Docker host")
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
