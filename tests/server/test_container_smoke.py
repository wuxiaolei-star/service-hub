"""Smoke coverage for the deployable Docker Compose service."""

import subprocess

import httpx
import pytest


@pytest.mark.integration
def test_compose_health_and_upload() -> None:
    """The AMD64 Compose service exposes health and accepts persistent file uploads."""
    subprocess.run(["docker", "compose", "up", "-d", "--build"], check=True)

    health = httpx.get("http://127.0.0.1:8000/api/v1/system/health", timeout=30)
    assert health.json() == {"status": "UP"}

    response = httpx.post(
        "http://127.0.0.1:8000/api/v1/files",
        files={"file": ("smoke.nc", b"data")},
        timeout=30,
    )
    assert response.status_code == 201
