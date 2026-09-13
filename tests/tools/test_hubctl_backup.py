"""Backup command contract tests for hubctl (create via API, local restore checklist)."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.tools.test_hubctl import load_hubctl


class FakeTransport:
    def __init__(self, responses: dict[str, object] | None = None) -> None:
        self.token: str | None = None
        self.responses = responses or {}
        self.json_calls: list[tuple[str, str, object | None]] = []

    def request_json(self, method: str, path: str, body: object | None) -> object:
        self.json_calls.append((method, path, body))
        value = self.responses.get(path, {"ok": True})
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def hubctl() -> ModuleType:
    return load_hubctl()


def test_backup_create_posts_to_system_backup(
    hubctl: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = {
        "file_name": "backup-20260913T000000Z.tar.gz",
        "size_bytes": 2048,
        "download_hint": "备份文件位于数据目录 backups/ 下",
    }
    transport = FakeTransport({"/api/v1/system/backup": payload})

    exit_code = hubctl.main(["backup", "create"], transport=transport)

    assert exit_code == 0
    assert transport.json_calls[0][:2] == ("POST", "/api/v1/system/backup")
    output = json.loads(capsys.readouterr().out)
    assert output == payload


def test_backup_restore_prints_local_checklist_without_network(
    hubctl: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class NoNetworkTransport(FakeTransport):
        def request_json(self, method: str, path: str, body: object | None) -> object:
            raise AssertionError("backup restore must not call the API")

    archive = tmp_path / "backup-20260913T000000Z.tar.gz"

    exit_code = hubctl.main(
        ["backup", "restore", "--file", str(archive)], transport=NoNetworkTransport()
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert str(archive) in output
    for keyword in ("停止", "解包", "恢复", "chown", "重启", "down -v"):
        assert keyword in output, f"missing {keyword!r} in restore checklist"
