"""Registry command contract tests for hubctl (list/pull/sync)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.tools.test_hubctl import load_hubctl


class FakeTransport:
    def __init__(
        self,
        responses: dict[str, object] | None = None,
        download_payload: bytes = b"package-bytes",
    ) -> None:
        self.token: str | None = None
        self.responses = responses or {}
        self.json_calls: list[tuple[str, str, object | None]] = []
        self.downloads: list[tuple[str, Path]] = []
        self.download_payload = download_payload

    def request_json(self, method: str, path: str, body: object | None) -> object:
        self.json_calls.append((method, path, body))
        value = self.responses.get(path, {"ok": True})
        if isinstance(value, Exception):
            raise value
        return value

    def download(self, path: str, destination: Path) -> None:
        self.downloads.append((path, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.download_payload)


@pytest.fixture
def hubctl() -> ModuleType:
    return load_hubctl()


def test_registry_list_prints_api_json(
    hubctl: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "items": [
            {
                "plugin_id": "nc_to_shp",
                "plugin_name": "NC to Shapefile",
                "version": "1.0.0",
                "build_key": "plugin_build_1",
                "runtime_type": "conda-pack",
                "target_arch": "amd64",
                "status": "ENABLED",
                "package_sha256": "a" * 64,
            }
        ]
    }
    transport = FakeTransport({"/api/v1/registry/plugins": payload})

    exit_code = hubctl.main(["registry", "list"], transport=transport)

    assert exit_code == 0
    assert transport.json_calls[0][:2] == ("GET", "/api/v1/registry/plugins")
    assert json.loads(capsys.readouterr().out) == payload


def test_registry_pull_downloads_build_package_and_prints_checksum(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport(download_payload=b"exact-package-bytes")
    output_dir = tmp_path / "packages"

    exit_code = hubctl.main(
        ["registry", "pull", "plugin_build_abc123", "--output", str(output_dir)],
        transport=transport,
    )

    assert exit_code == 0
    assert transport.downloads == [
        ("/api/v1/registry/download/plugin_build_abc123", output_dir / "plugin_build_abc123.pypkg")
    ]
    saved = output_dir / "plugin_build_abc123.pypkg"
    assert saved.read_bytes() == b"exact-package-bytes"
    expected = hashlib.sha256(b"exact-package-bytes").hexdigest()
    output = capsys.readouterr().out
    assert f"{expected}  {saved}" in output


def test_registry_sync_downloads_only_enabled_builds(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def item(build_key: str, status: str) -> dict[str, str]:
        return {"build_key": build_key, "status": status}

    payload = {
        "items": [
            item("plugin_build_enabled_1", "ENABLED"),
            item("plugin_build_ready", "READY"),
            item("plugin_build_enabled_2", "ENABLED"),
            item("plugin_build_failed", "FAILED"),
        ]
    }
    transport = FakeTransport({"/api/v1/registry/plugins": payload})
    output_dir = tmp_path / "mirror"

    exit_code = hubctl.main(
        ["registry", "sync", "--output", str(output_dir)], transport=transport
    )

    assert exit_code == 0
    assert transport.json_calls[0][:2] == ("GET", "/api/v1/registry/plugins")
    assert [path for _path, path in transport.downloads] == [
        output_dir / "plugin_build_enabled_1.pypkg",
        output_dir / "plugin_build_enabled_2.pypkg",
    ]
    output = capsys.readouterr().out
    for build_key in ("plugin_build_enabled_1", "plugin_build_enabled_2"):
        destination = output_dir / f"{build_key}.pypkg"
        expected = hashlib.sha256(transport.download_payload).hexdigest()
        assert f"{expected}  {destination}" in output


def test_registry_pull_rejects_unsafe_build_key(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport()

    exit_code = hubctl.main(
        ["registry", "pull", "../escape", "--output", str(tmp_path)], transport=transport
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert transport.downloads == []
    assert captured.err
