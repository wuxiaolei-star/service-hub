from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load_hubctl() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("hubctl_under_test", "tools/hubctl")
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


@pytest.fixture
def hubctl() -> ModuleType:
    return load_hubctl()


class FakeTransport:
    def __init__(self, response: object | None = None) -> None:
        self.response = {"ok": True} if response is None else response
        self.json_calls: list[tuple[str, str, object | None]] = []
        self.uploads: list[tuple[str, Path]] = []
        self.downloads: list[tuple[str, Path]] = []

    @property
    def json_body(self) -> object | None:
        return self.json_calls[-1][2]

    def request_json(self, method: str, path: str, body: object | None) -> object:
        self.json_calls.append((method, path, body))
        return self.response

    def upload(self, path: str, file_path: Path) -> object:
        self.uploads.append((path, file_path))
        return self.response

    def download(self, path: str, destination: Path) -> None:
        self.downloads.append((path, destination))


def test_job_run_builds_current_api_payload(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Omitting runtime_type or misparsing repeated inputs would send an invalid Job request."""
    params = tmp_path / "params.json"
    params.write_text('{"group_name":"1"}', encoding="utf-8")
    fake_transport = FakeTransport({"job_id": "job_123", "status": "PENDING"})

    exit_code = hubctl.main(
        [
            "job",
            "run",
            "nc_to_shp",
            "--version",
            "1.0.0",
            "--runtime",
            "conda-pack",
            "--input",
            "source_nc=file_123",
            "--params",
            str(params),
        ],
        transport=fake_transport,
    )

    assert exit_code == 0
    assert fake_transport.json_calls == [
        (
            "POST",
            "/api/v1/jobs",
            {
                "plugin_id": "nc_to_shp",
                "version": "1.0.0",
                "runtime_type": "conda-pack",
                "inputs": {"source_nc": "file_123"},
                "params": {"group_name": "1"},
            },
        )
    ]
    assert json.loads(capsys.readouterr().out) == {"job_id": "job_123", "status": "PENDING"}


@pytest.mark.parametrize(
    ("argv", "call"),
    [
        (["health"], ("GET", "/api/v1/system/health", None)),
        (["plugin", "list"], ("GET", "/api/v1/plugins", None)),
        (
            ["plugin", "enable", "plugin_build_123"],
            ("POST", "/api/v1/plugin-builds/plugin_build_123/enable", None),
        ),
        (["job", "status", "job_123"], ("GET", "/api/v1/jobs/job_123", None)),
        (["job", "logs", "job_123"], ("GET", "/api/v1/jobs/job_123/logs", None)),
        (["job", "cancel", "job_123"], ("POST", "/api/v1/jobs/job_123/cancel", None)),
    ],
)
def test_json_commands_use_current_api_paths(
    hubctl: ModuleType, argv: list[str], call: tuple[str, str, object | None]
) -> None:
    """A stale path such as plugins/{id}/{version}/enable would miss the implemented routers."""
    fake_transport = FakeTransport()

    assert hubctl.main(argv, transport=fake_transport) == 0

    assert fake_transport.json_calls == [call]


def test_logs_can_pass_cursor_and_limit(hubctl: ModuleType) -> None:
    """Dropping log pagination options would prevent clients from using the current logs API."""
    fake_transport = FakeTransport()

    assert (
        hubctl.main(
            ["job", "logs", "job_123", "--cursor", "25", "--limit", "50"],
            transport=fake_transport,
        )
        == 0
    )

    assert fake_transport.json_calls == [
        ("GET", "/api/v1/jobs/job_123/logs?cursor=25&limit=50", None)
    ]


@pytest.mark.parametrize(
    ("argv", "upload_path"),
    [
        (["plugin", "install"], "/api/v1/plugins/install"),
        (["file", "upload"], "/api/v1/files"),
    ],
)
def test_upload_commands_use_multipart_paths(
    hubctl: ModuleType, tmp_path: Path, argv: list[str], upload_path: str
) -> None:
    """Sending package/file bytes as JSON would bypass the server's multipart endpoints."""
    source = tmp_path / "payload.bin"
    source.write_bytes(b"payload")
    fake_transport = FakeTransport({"file_id": "file_123"})

    assert hubctl.main([*argv, str(source)], transport=fake_transport) == 0

    assert fake_transport.uploads == [(upload_path, source)]


def test_hub_error_prints_code_and_message(
    hubctl: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ignoring Hub's safe error envelope would hide the actionable code from operators."""

    class ErrorTransport(FakeTransport):
        def request_json(self, method: str, path: str, body: object | None) -> object:
            raise hubctl.HubApiError("PLUGIN_NOT_FOUND", "插件不存在")

    assert hubctl.main(["plugin", "list"], transport=ErrorTransport()) == 1

    assert capsys.readouterr().err == "PLUGIN_NOT_FOUND: 插件不存在\n"


def test_job_download_fetches_outputs_and_refuses_to_overwrite(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Overwriting an existing output would destroy caller data during batch downloads."""
    existing = tmp_path / "result_files.zip"
    existing.write_bytes(b"old")
    fake_transport = FakeTransport(
        {
            "items": [
                {"file_id": "file_1", "name": "result_files", "extension": ".zip"},
                {"file_id": "file_2", "name": "../unsafe", "extension": ".txt"},
            ]
        }
    )

    assert (
        hubctl.main(
            ["job", "download", "job_123", "--output", str(tmp_path)],
            transport=fake_transport,
        )
        == 1
    )

    assert fake_transport.json_calls == [("GET", "/api/v1/jobs/job_123/outputs", None)]
    assert fake_transport.downloads == []
    assert "refusing to overwrite" in capsys.readouterr().err


def test_job_download_preserves_schema_extension(hubctl: ModuleType, tmp_path: Path) -> None:
    """Ignoring FileResponse.extension would download result_files without its .zip suffix."""
    fake_transport = FakeTransport(
        {"items": [{"file_id": "file_1", "name": "result_files", "extension": ".zip"}]}
    )

    assert (
        hubctl.main(
            ["job", "download", "job_123", "--output", str(tmp_path)],
            transport=fake_transport,
        )
        == 0
    )

    assert fake_transport.downloads == [
        ("/api/v1/files/file_1/download", tmp_path / "result_files.zip")
    ]


def test_job_download_uses_safe_filenames_and_download_endpoint(
    hubctl: ModuleType, tmp_path: Path
) -> None:
    """Server names must not escape the target dir."""
    fake_transport = FakeTransport(
        {
            "items": [
                {"file_id": "file_1", "name": "result", "extension": ".zip"},
                {"file_id": "file_2", "name": "../unsafe", "extension": ".txt"},
            ]
        }
    )

    assert (
        hubctl.main(
            ["job", "download", "job_123", "--output", str(tmp_path)],
            transport=fake_transport,
        )
        == 0
    )

    assert fake_transport.downloads == [
        ("/api/v1/files/file_1/download", tmp_path / "result.zip"),
        ("/api/v1/files/file_2/download", tmp_path / "file_2.txt"),
    ]


@pytest.mark.parametrize("character", ["\r", "\n", "\x00", "\x1f", "\x7f"])
def test_http_transport_rejects_control_characters_in_multipart_filename(
    hubctl: ModuleType, character: str
) -> None:
    """Allowing CR/LF/NUL in filename would corrupt the multipart Content-Disposition header."""

    class BadUploadPath:
        name = f"bad{character}name.pypkg"

    transport = hubctl.HttpTransport("http://127.0.0.1:8000")

    with pytest.raises(ValueError, match="control characters"):
        transport.upload("/api/v1/plugins/install", BadUploadPath())


def test_http_transport_multipart_streams_file_with_content_length(
    hubctl: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading the whole upload into memory would break large-file usage and hide Content-Length."""
    source = tmp_path / "large.pypkg"
    source.write_bytes(b"0123456789" * 1024)
    requests: list[dict[str, Any]] = []

    class GuardedFile(io.BufferedReader):
        def read(self, size: int | None = -1) -> bytes:
            if size is None or size < 0:
                raise AssertionError("upload must read bounded chunks")
            return super().read(size)

    original_open = Path.open

    def guarded_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        opened: Any = original_open(self, mode, buffering, encoding, errors, newline)
        if self == source and "b" in mode and "r" in mode:
            raw = opened.detach()
            return GuardedFile(raw)
        return opened

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.method = ""
            self.path = ""
            self.headers: dict[str, str] = {}
            self.sent: list[bytes] = []

        def putrequest(self, method: str, path: str) -> None:
            self.method = method
            self.path = path

        def putheader(self, name: str, value: str) -> None:
            self.headers[name] = value

        def endheaders(self) -> None:
            pass

        def send(self, data: bytes) -> None:
            self.sent.append(data)

        def getresponse(self) -> object:
            requests.append(
                {
                    "method": self.method,
                    "path": self.path,
                    "headers": self.headers,
                    "sent_lengths": [len(chunk) for chunk in self.sent],
                    "body": b"".join(self.sent),
                }
            )
            return FakeResponse(202, b'{"build_id":"plugin_build_123"}')

        def close(self) -> None:
            pass

    class FakeResponse:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self.reason = "Accepted"
            self._body = body

        def read(self) -> bytes:
            return self._body

    monkeypatch.setattr(hubctl.http.client, "HTTPConnection", FakeConnection)
    monkeypatch.setattr(hubctl.uuid, "uuid4", lambda: "fixed-boundary")
    monkeypatch.setattr(Path, "open", guarded_open)
    transport = hubctl.HttpTransport("http://127.0.0.1:8000")

    assert transport.upload("/api/v1/plugins/install", source) == {"build_id": "plugin_build_123"}

    request = requests[0]
    content_length = int(request["headers"]["Content-Length"])
    assert request["method"] == "POST"
    assert request["path"] == "/api/v1/plugins/install"
    assert request["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert content_length == len(request["body"])
    assert request["sent_lengths"][1:-1] == [8192, 2048]
