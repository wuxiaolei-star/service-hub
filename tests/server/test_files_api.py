"""HTTP contract tests for Hub file uploads and retrieval."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.settings import (
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    StorageSettings,
    UploadSettings,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Serve the file API against isolated durable storage."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_upload_then_get_metadata_and_download(client: TestClient) -> None:
    """A stored upload must retain public metadata and exact downloadable bytes."""
    created = client.post(
        "/api/v1/files",
        files={"file": ("model.NC", b"netcdf-data", "application/x-netcdf")},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "model.NC"
    assert body["extension"] == ".nc"
    file_key = body["file_id"]
    assert client.get(f"/api/v1/files/{file_key}").json()["sha256"] == body["sha256"]
    assert client.get(f"/api/v1/files/{file_key}/download").content == b"netcdf-data"


def test_missing_file_uses_stable_error_shape(client: TestClient) -> None:
    """A missing lookup must preserve the documented error contract rather than a framework 404."""
    response = client.get("/api/v1/files/file_missing")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {
            "code": "FILE_NOT_FOUND",
            "message": "文件 file_missing 不存在",
            "details": None,
        },
    }


def test_rejects_upload_larger_than_limit(client: TestClient) -> None:
    """An over-limit multipart body must reach the storage limit error rather than be retained."""
    response = client.post("/api/v1/files", files={"file": ("a.nc", b"0" * 1025)})

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_unexpected_errors_use_stable_error_shape(tmp_path: Path) -> None:
    """An unhandled exception must not expose traceback details or storage paths to clients."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10),
    )
    app = create_app(settings)

    @app.get("/raise-unexpected-error")
    def raise_unexpected_error() -> None:
        raise RuntimeError(f"storage unavailable at {tmp_path}")

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/raise-unexpected-error")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": {"code": "UNEXPECTED_ERROR", "message": "服务器内部错误", "details": None},
    }
