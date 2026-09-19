"""Disk-space precheck for backup creation (409 DISK_SPACE_INSUFFICIENT)."""

from __future__ import annotations

import shutil
import sqlite3
from collections import namedtuple
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.errors import HubError
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.services.auth import AuthService
from hub_server.services.backup import BackupService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RetentionSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)

_Usage = namedtuple("_Usage", ["total", "used", "free"])


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        retention=RetentionSettings(backup_keep=3),
    )


def _service(tmp_path: Path) -> BackupService:
    return BackupService(
        db_path=tmp_path / "hub.db",
        storage_root=tmp_path / "data",
        keep=3,
    )


def _usage(free: int) -> _Usage:  # type: ignore[type-arg]
    return _Usage(total=free + 1000, used=1000, free=free)


def _seed_data(storage_root: Path) -> None:
    seeded = storage_root / "files"
    seeded.mkdir(parents=True)
    (seeded / "seed.bin").write_bytes(b"x" * 100)


def _seed_database(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE marker (x INTEGER)")


def _admin_token(client: TestClient) -> str:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return token


def test_backup_refused_when_free_space_below_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_data(tmp_path / "data")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _usage(free=10))

    with pytest.raises(HubError) as raised:
        _service(tmp_path).create_backup()

    error = raised.value
    assert error.code == "DISK_SPACE_INSUFFICIENT"
    assert error.status_code == 409
    assert error.details is not None
    assert error.details["free"] == 10
    assert error.details["needed"] == 130  # 100 bytes of data x 1.3 safety factor
    backup_dir = tmp_path / "data" / "backups"
    assert list(backup_dir.glob("backup-*.tar.gz")) == []


def test_estimate_excludes_the_backups_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_data(tmp_path / "data")
    backup_dir = tmp_path / "data" / "backups"
    backup_dir.mkdir(parents=True)
    (backup_dir / "backup-old.tar.gz").write_bytes(b"y" * 100_000)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _usage(free=10))

    with pytest.raises(HubError) as raised:
        _service(tmp_path).create_backup()

    details = raised.value.details
    assert details is not None
    assert details["needed"] == 130  # previous archives do not count towards the estimate


def test_backup_proceeds_when_free_space_is_sufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_data(tmp_path / "data")
    _seed_database(tmp_path / "hub.db")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _usage(free=10**9))

    result = _service(tmp_path).create_backup()

    assert (tmp_path / "data" / "backups" / result.file_name).is_file()


def test_backup_api_maps_shortage_to_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: _usage(free=10))
    with TestClient(create_app(_settings(tmp_path))) as client:
        token = _admin_token(client)

        response = client.post(
            "/api/v1/system/backup", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "DISK_SPACE_INSUFFICIENT"
    assert body["error"]["details"]["free"] == 10
    assert "needed" in body["error"]["details"]
