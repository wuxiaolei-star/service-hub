"""Web console lifecycle and boundary acceptance on the disposable Compose stack."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _compose_command(project_name: str, *arguments: str, compose_file: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project_name,
        "--project-directory",
        str(REPOSITORY_ROOT),
        "--file",
        str(compose_file),
        *arguments,
    ]


def _available_local_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as handle:
        handle.bind(("127.0.0.1", 0))
        return int(handle.getsockname()[1])


def _wait_for_health(url: str) -> httpx.Response:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
        except httpx.HTTPError:
            time.sleep(0.5)
            continue
        if response.status_code == 200:
            return response
        time.sleep(0.5)
    raise AssertionError(f"endpoint did not become healthy within 60 seconds: {url}")


@pytest.mark.integration
def test_web_console_serves_spa_and_enforces_boundaries(tmp_path: Path) -> None:
    """The Web image must serve the SPA, proxy /api/v1, deny /internal/v1, and survive restart."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("Web console lifecycle requires a native Linux AMD64 Docker host")

    compose_model = yaml.safe_load((REPOSITORY_ROOT / "compose.yaml").read_text("utf-8"))
    assert {"service-hub", "service-hub-web"} <= set(compose_model["services"])
    # V3.0 keeps the Docker Socket and the data directory inside the backend only.
    assert (
        "/var/run/docker.sock:/var/run/docker.sock"
        in compose_model["services"]["service-hub"]["volumes"]
    )
    assert not compose_model["services"]["service-hub-web"].get("volumes")

    project_name = f"hub-web-{uuid.uuid4().hex}"
    backend_port = _available_local_port()
    web_port = _available_local_port()
    host_data_dir = tmp_path / "data"
    host_data_dir.mkdir()
    absolute_data_dir = str(host_data_dir.resolve())

    compose_file = tmp_path / "compose.web-smoke.yaml"
    compose_file.write_text(
        "services:\n"
        "  service-hub:\n"
        "    build:\n"
        f"      context: {REPOSITORY_ROOT.as_posix()}\n"
        "      dockerfile: Dockerfile\n"
        "    image: python-service-hub:1.0.0-linux-amd64\n"
        "    platform: linux/amd64\n"
        "    environment:\n"
        f"      HUB_HOST_DATA_DIR: {absolute_data_dir}\n"
        f"    ports:\n      - 127.0.0.1:{backend_port}:8000\n"
        "    volumes:\n"
        f"      - {absolute_data_dir}:/data\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n"
        "  service-hub-web:\n"
        "    build:\n"
        f"      context: {(REPOSITORY_ROOT / 'web').as_posix()}\n"
        "      dockerfile: Dockerfile\n"
        "    image: python-service-hub-web:1.0.0-linux-amd64\n"
        "    platform: linux/amd64\n"
        "    depends_on:\n"
        "      service-hub:\n"
        "        condition: service_healthy\n"
        f"    ports:\n      - 127.0.0.1:{web_port}:8080\n",
        encoding="utf-8",
    )

    base = f"http://127.0.0.1:{web_port}"
    try:
        subprocess.run(
            _compose_command(project_name, "up", "-d", "--build", compose_file=compose_file),
            check=True,
            cwd=REPOSITORY_ROOT,
        )

        _wait_for_health(f"{base}/api/v1/system/health")

        root = httpx.get(f"{base}/", timeout=30)
        assert root.status_code == 200
        assert "text/html" in root.headers.get("content-type", "")

        spa_refresh = httpx.get(f"{base}/jobs/nonexistent", timeout=30)
        assert spa_refresh.status_code == 200
        assert "text/html" in spa_refresh.headers.get("content-type", "")

        proxied_health = httpx.get(f"{base}/api/v1/system/health", timeout=30)
        assert proxied_health.json() == {"status": "UP"}

        internal = httpx.get(f"{base}/internal/v1/system/health", timeout=30)
        assert internal.status_code == 404

        # The Web container must never see the data directory or the Docker socket.
        web_container = subprocess.run(
            _compose_command(
                project_name,
                "ps",
                "-q",
                "service-hub-web",
                compose_file=compose_file,
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert web_container, "service-hub-web container is not running"
        web_mounts = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", web_container],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "/data" not in web_mounts
        assert "docker.sock" not in web_mounts

        # V3.0: the long-running service manager must be up, and only the two socket
        # consumers may hold the Docker socket GID inside the backend container.
        manager_status = subprocess.run(
            _compose_command(
                project_name,
                "exec",
                "-T",
                "service-hub",
                "supervisorctl",
                "-c",
                "/etc/service-hub/supervisord.conf",
                "status",
                compose_file=compose_file,
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "service-manager" in manager_status
        assert "RUNNING" in manager_status

        socket_gid = subprocess.run(
            _compose_command(
                project_name,
                "exec",
                "-T",
                "service-hub",
                "stat",
                "-c",
                "%g",
                "/var/run/docker.sock",
                compose_file=compose_file,
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        def _supplementary_groups(account: str) -> set[str]:
            raw = subprocess.run(
                _compose_command(
                    project_name,
                    "exec",
                    "-T",
                    "service-hub",
                    "id",
                    "-G",
                    account,
                    compose_file=compose_file,
                ),
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return set(raw.split())

        assert socket_gid in _supplementary_groups("docker-runner")
        assert socket_gid in _supplementary_groups("service-mgr")
        assert socket_gid not in _supplementary_groups("hub-api")
        assert socket_gid not in _supplementary_groups("conda-runner")

        # V2 auth is mandatory, so the console's own proxy must carry a token through. This
        # stack is disposable and bootstraps its own admin, so the credentials come from its
        # own data directory rather than from the operator environment.
        credentials = subprocess.run(
            _compose_command(
                project_name,
                "exec",
                "-T",
                "service-hub",
                "cat",
                "/data/bootstrap-admin.json",
                compose_file=compose_file,
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        bootstrap = json.loads(credentials)
        login = httpx.post(
            f"{base}/api/v1/auth/login",
            json={
                "username": bootstrap["username"],
                "password": bootstrap["password"],
            },
            timeout=30,
        )
        assert login.status_code == 200, login.text
        console_headers = {"Authorization": f"Bearer {login.json()['token']}"}

        uploaded = httpx.post(
            f"{base}/api/v1/files",
            headers=console_headers,
            files={"file": ("web-smoke.nc", b"data")},
            timeout=30,
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = str(uploaded.json()["file_id"])

        subprocess.run(
            _compose_command(project_name, "restart", compose_file=compose_file),
            check=True,
            cwd=REPOSITORY_ROOT,
        )
        _wait_for_health(f"{base}/api/v1/system/health")

        metadata = httpx.get(
            f"{base}/api/v1/files/{file_id}", headers=console_headers, timeout=30
        )
        assert metadata.status_code == 200
        assert metadata.json()["file_id"] == file_id
    finally:
        subprocess.run(
            _compose_command(
                project_name,
                "down",
                "--volumes",
                "--remove-orphans",
                compose_file=compose_file,
            ),
            check=False,
            cwd=REPOSITORY_ROOT,
        )
