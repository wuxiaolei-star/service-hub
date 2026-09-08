import sys

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.settings import (
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


@pytest.fixture
def settings() -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root="/var/lib/hub"),
        database=DatabaseSettings(url="sqlite:////var/lib/hub/db/hub.db"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        platform_os="linux",
        platform_arch="amd64",
    )


@pytest.fixture
def client(settings: HubSettings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_health_and_info_report_configured_platform(client: TestClient) -> None:
    """System endpoints expose health and configured deployment metadata."""
    assert client.get("/api/v1/system/health").json() == {"status": "UP"}

    info = client.get("/api/v1/system/info").json()

    assert info["hub_version"] == "0.1.0"
    assert info["platform"] == {"os": "linux", "arch": "amd64"}
    assert info["python_version"] == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert info["deployment_mode"] == "offline"


def test_create_app_retains_explicit_settings(settings: HubSettings) -> None:
    """An explicitly supplied configuration is available to server components."""
    app = create_app(settings)

    assert app.state.settings is settings
