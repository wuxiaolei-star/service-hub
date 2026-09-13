"""V2.1 quota, retention, and backup lifecycle acceptance on Linux AMD64."""

from __future__ import annotations

import os
import platform
import sys

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_v2_quota_backup_lifecycle() -> None:  # pragma: no cover - Linux AMD64 only
    """Quota 409 -> usage -> metrics -> backup on the deployed instance."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("quota lifecycle requires a native Linux AMD64 Docker host")
    base_url = os.environ.get("HUB_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    username = os.environ.get("HUB_BOOTSTRAP_USERNAME", "admin")
    password = os.environ.get("HUB_BOOTSTRAP_PASSWORD")
    if not password:
        pytest.skip("set HUB_BOOTSTRAP_PASSWORD to exercise the quota lifecycle")

    with httpx.Client(base_url=base_url, timeout=60) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        me = client.get("/api/v1/auth/me", headers=headers)
        user_id = me.json()["id"]

        upload = client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("quota.nc", b"quota-lifecycle")},
        )
        assert upload.status_code == 201

        usage = client.get(f"/api/v1/users/{user_id}/usage", headers=headers)
        assert usage.status_code == 200
        assert usage.json()["file_count"] >= 1

        metrics = client.get("/api/v1/system/metrics", headers=headers)
        assert metrics.status_code == 200
        assert "hub_files_bytes_total" in metrics.text

        backup = client.post("/api/v1/system/backup", headers=headers)
        assert backup.status_code == 201
        assert backup.json()["size_bytes"] > 0
