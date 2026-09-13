"""HTTP contract tests for Hub file uploads and retrieval."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import FileRecord
from hub_server.settings import (
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Serve the file API against isolated durable storage."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
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


def test_file_list_returns_the_100_newest_records_with_utc_timestamps(client: TestClient) -> None:
    """Removing the limit or descending sort would make the file list unbounded or stale."""
    for index in range(101):
        created = client.post(
            "/api/v1/files",
            files={"file": (f"file-{index}.nc", b"x", "application/x-netcdf")},
        )
        assert created.status_code == 201

    files = client.get("/api/v1/files")

    assert files.status_code == 200
    items = files.json()["items"]
    assert len(items) == 100
    assert [item["name"] for item in items] == [f"file-{index}.nc" for index in range(100, 0, -1)]
    assert items[0]["created_at"].endswith("Z")


def test_file_list_excludes_newer_deleted_records_before_applying_limit(
    client: TestClient,
) -> None:
    """Limiting before filtering would hide an available file behind newer deleted records."""
    created = client.post(
        "/api/v1/files",
        files={"file": ("available.nc", b"available", "application/x-netcdf")},
    )
    assert created.status_code == 201
    available_file_id = created.json()["file_id"]
    oldest_deleted_at = datetime(2026, 1, 2, tzinfo=UTC)

    with client.app.state.session_factory() as session:
        available = session.query(FileRecord).filter_by(file_key=available_file_id).one()
        available.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.add_all(
            [
                FileRecord(
                    file_key=f"deleted_{index}",
                    logical_name=f"deleted-{index}.nc",
                    original_filename=f"deleted-{index}.nc",
                    relative_path=f"deleted/{index}.nc",
                    extension=".nc",
                    mime_type="application/x-netcdf",
                    size_bytes=1,
                    sha256=f"{index:064x}",
                    status="DELETED",
                    created_at=oldest_deleted_at + timedelta(seconds=index),
                )
                for index in range(100)
            ]
        )
        session.commit()

    files = client.get("/api/v1/files")

    assert files.status_code == 200
    assert [item["file_id"] for item in files.json()["items"]] == [available_file_id]


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


def test_upload_accepts_file_exactly_at_size_limit_despite_multipart_overhead(
    client: TestClient,
) -> None:
    """Multipart headers and boundaries must not reduce the configured file-content limit."""
    payload = b"0" * 1024

    response = client.post("/api/v1/files", files={"file": ("limit.nc", payload)})

    assert response.status_code == 201
    assert response.json()["size"] == 1024


def test_malformed_multipart_after_file_part_removes_partial_upload(
    client: TestClient, tmp_path: Path
) -> None:
    """A parser error after file bytes arrive must leave no controlled temporary directory."""
    boundary = b"malformed-boundary"
    body = (
        b"--malformed-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="partial.nc"\r\n'
        b"Content-Type: application/x-netcdf\r\n\r\n"
        b"partial-file-bytes\r\n--malformed-boundary\r\n"
        b"Invalid Header: parser-must-reject\r\n\r\n"
    )

    response = client.post(
        "/api/v1/files",
        content=body,
        headers={"content-type": "multipart/form-data; boundary=" + boundary.decode()},
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": {"code": "REQUEST_VALIDATION_ERROR", "message": "请求参数无效", "details": None},
    }
    assert not list((tmp_path / "data" / "uploads").glob("file_*.tmp-*"))


def test_form_parser_error_after_file_write_uses_validation_error_and_cleans_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-Multipart parser failure after a write must be client-safe and leave no temp data."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
    )
    original_write = MultipartParser.write

    def write_then_raise(parser: MultipartParser, data: bytes) -> int:
        original_write(parser, data)
        raise FormParserError("simulated parser failure after file write")

    monkeypatch.setattr(MultipartParser, "write", write_then_raise)

    with TestClient(create_app(settings), raise_server_exceptions=False) as test_client:
        response = test_client.post("/api/v1/files", files={"file": ("partial.nc", b"data")})

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": {"code": "REQUEST_VALIDATION_ERROR", "message": "请求参数无效", "details": None},
    }
    assert not list((tmp_path / "data" / "uploads").glob("file_*.tmp-*"))


def test_rejects_oversized_multipart_boundary_with_validation_error(client: TestClient) -> None:
    """A parser-construction error must use the public malformed-input response."""
    boundary = "a" * 257

    response = client.post(
        "/api/v1/files",
        content=b"",
        headers={"content-type": "multipart/form-data; boundary=" + boundary},
    )

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": {"code": "REQUEST_VALIDATION_ERROR", "message": "请求参数无效", "details": None},
    }


def test_rejects_chunked_oversize_at_stream_boundary_without_uploadfile_spool(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undeclared over-limit body must stop at a raw request chunk, before Starlette spools it.

    Replacing the framework's UploadFile constructor ensures the route never asks Starlette to
    parse into its temporary file.  A final multipart chunk remains unread, proving the size
    check runs at the controlled stream boundary rather than after a full-body read.
    """
    from starlette.datastructures import UploadFile

    def fail_if_framework_spools(*_: object, **__: object) -> None:
        raise AssertionError("Starlette UploadFile spool must not be created")

    monkeypatch.setattr(UploadFile, "__init__", fail_if_framework_spools)
    boundary = b"stream-boundary"
    chunks = [
        b"--stream-boundary\r\n"
        b'Content-Disposition: form-data; name="file"; filename="large.nc"\r\n'
        b"Content-Type: application/x-netcdf\r\n\r\n",
        b"a" * 500,
        b"b" * 600,
        b"\r\n--stream-boundary--\r\n",
    ]
    received_chunks = 0
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        nonlocal received_chunks
        chunk = chunks[received_chunks]
        received_chunks += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": received_chunks < len(chunks),
        }

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    async def send_request() -> None:
        await client.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/files",
                "raw_path": b"/api/v1/files",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"host", b"testserver"),
                    (b"content-type", b"multipart/form-data; boundary=" + boundary),
                ],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )

    client.portal.call(send_request)

    response_start = next(
        message for message in messages if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message["body"]
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert received_chunks == 3
    assert response_start["status"] == 413
    assert json.loads(response_body) == {
        "success": False,
        "error": {
            "code": "UPLOAD_TOO_LARGE",
            "message": "上传文件超过大小限制",
            "details": {"max_size_bytes": 1024},
        },
    }


def test_missing_multipart_file_uses_stable_validation_error(client: TestClient) -> None:
    """A missing multipart file must not expose FastAPI validation output."""
    response = client.post("/api/v1/files", files={"different": ("a.nc", b"data")})

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": {"code": "REQUEST_VALIDATION_ERROR", "message": "请求参数无效", "details": None},
    }


def test_unknown_route_uses_stable_http_error(client: TestClient) -> None:
    """Framework-level routing failures must use the same client-safe envelope as Hub errors."""
    response = client.get("/api/v1/not-a-route")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {"code": "HTTP_NOT_FOUND", "message": "请求的资源不存在", "details": None},
    }


def test_request_validation_error_uses_stable_error_shape(client: TestClient) -> None:
    """Framework coercion failures must not expose FastAPI's default validation payload."""

    @client.app.get("/requires-integer")
    def requires_integer(value: int) -> dict[str, int]:
        return {"value": value}

    response = client.get("/requires-integer?value=not-an-integer")

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": {"code": "REQUEST_VALIDATION_ERROR", "message": "请求参数无效", "details": None},
    }


def test_http_exception_uses_stable_error_shape(client: TestClient) -> None:
    """A route-raised HTTPException must not leak its framework detail body."""

    @client.app.get("/raises-http-error")
    def raises_http_error() -> None:
        raise HTTPException(status_code=409, detail="internal conflict detail")

    response = client.get("/raises-http-error")

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": {"code": "HTTP_ERROR", "message": "请求失败", "details": None},
    }


def test_unexpected_errors_use_stable_error_shape(tmp_path: Path) -> None:
    """An unhandled exception must not expose traceback details or storage paths to clients."""
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
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
