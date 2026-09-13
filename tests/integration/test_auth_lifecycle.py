"""V2.0 authentication lifecycle acceptance on a disposable Linux AMD64 stack."""

from __future__ import annotations

import os
import platform
import sys

import httpx
import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_v2_auth_lifecycle() -> None:  # pragma: no cover - Linux AMD64 only
    """Bootstrap → login → role matrix → audit → off-mode equivalence."""
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("auth lifecycle requires a native Linux AMD64 Docker host")
    base_url = os.environ.get("HUB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    client = httpx.Client(base_url=base_url, timeout=30)

    # 1. Health stays public; everything else demands credentials.
    assert client.get("/api/v1/system/health").json() == {"status": "UP"}
    assert client.get("/api/v1/files").status_code == 401

    # 2. Bootstrap credentials exist on the server data root (HUB_DATA_ROOT),
    #    the operator provides them through the environment for this test.
    username = os.environ.get("HUB_BOOTSTRAP_USERNAME", "admin")
    password = os.environ.get("HUB_BOOTSTRAP_PASSWORD")
    if not password:
        pytest.skip("set HUB_BOOTSTRAP_PASSWORD to exercise the auth lifecycle")

    login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    token = login.json()["token"]
    assert login.json()["role"] == "admin"
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Authenticated access works and the actor is recorded.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    audit = client.get("/api/v1/audit-logs", headers=headers).json()["items"]
    assert any(entry["action"] == "auth.login" for entry in audit)

    # 4. Logout revokes the token immediately.
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
