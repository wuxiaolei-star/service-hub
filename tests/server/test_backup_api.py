"""Backup endpoint contract tests (admin only, tar.gz snapshots under backups/)."""

from __future__ import annotations

import re
import sqlite3
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, UserRecord
from hub_server.services.auth import AuthService
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

_BACKUP_PATH = "/api/v1/system/backup"


def _settings(tmp_path: Path, *, backup_keep: int = 3) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        retention=RetentionSettings(backup_keep=backup_keep),
    )


def _admin_headers(client: TestClient) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _operator_headers(client: TestClient, headers: dict[str, str]) -> dict[str, str]:
    created = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "op", "password": "long-enough-pass", "role": "operator"},
    )
    assert created.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "op", "password": "long-enough-pass"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "data" / "backups"


def test_backup_requires_admin(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        anonymous = client.post(_BACKUP_PATH)
        assert anonymous.status_code == 401

        admin = _admin_headers(client)
        operator = _operator_headers(client, admin)
        forbidden = client.post(_BACKUP_PATH, headers=operator)
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "FORBIDDEN"


def test_backup_creates_archive_with_openable_snapshot(tmp_path: Path) -> None:
    seeded = tmp_path / "data" / "files"
    seeded.mkdir(parents=True)
    (seeded / "seed.bin").write_bytes(b"seeded-file")
    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = _admin_headers(client)
        uploaded = client.post(
            "/api/v1/files",
            headers=headers,
            files={"file": ("input.txt", b"hub-backup-content", "text/plain")},
        )
        assert uploaded.status_code == 201

        response = client.post(_BACKUP_PATH, headers=headers)

        assert response.status_code == 201
        payload = response.json()
        assert re.fullmatch(r"backup-\d{8}T\d{6}Z\.tar\.gz", payload["file_name"])
        archive_path = _backup_dir(tmp_path) / payload["file_name"]
        assert archive_path.is_file()
        assert payload["size_bytes"] == archive_path.stat().st_size
        assert "backups" in payload["download_hint"]

        with tarfile.open(archive_path, "r:gz") as archive:
            names = archive.getnames()
            member = archive.extractfile("hub.db")
            assert member is not None
            snapshot_bytes = member.read()
        assert "hub.db" in names
        assert "files/seed.bin" in names
        assert any(name.startswith("uploads/") for name in names)
        assert not any(
            name == "environments" or name.startswith("logs") or name.startswith("backups")
            for name in names
        )

        snapshot_path = tmp_path / "snapshot.db"
        snapshot_path.write_bytes(snapshot_bytes)
        with sqlite3.connect(snapshot_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            usernames = [row[0] for row in connection.execute("SELECT username FROM users")]
        assert "users" in tables
        assert "admin" in usernames


def test_backup_prunes_oldest_beyond_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hub_server.services.backup as backup_module

    tick = {"second": 0}

    def fake_now() -> datetime:
        tick["second"] += 1
        return datetime(2026, 1, 1, 0, 0, tick["second"], tzinfo=UTC)

    monkeypatch.setattr(backup_module, "_utc_now", fake_now)

    with TestClient(create_app(_settings(tmp_path, backup_keep=2))) as client:
        headers = _admin_headers(client)

        def created() -> str:
            return client.post(_BACKUP_PATH, headers=headers).json()["file_name"]

        first = created()
        second = created()
        third = created()

        names = {path.name for path in _backup_dir(tmp_path).glob("backup-*.tar.gz")}
        assert names == {second, third}
        assert first not in names


def test_backup_is_audited(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = _admin_headers(client)
        payload = client.post(_BACKUP_PATH, headers=headers).json()

        factory = client.app.state.session_factory
        with factory() as session:
            entry = (
                session.query(AuditLogRecord)
                .filter(AuditLogRecord.action == "system.backup")
                .one()
            )
        assert entry.resource_type == "backup"
        assert entry.resource_id == payload["file_name"]
        assert entry.actor_name == "admin"
        assert entry.actor_type == "user"
        assert entry.result == "ok"
