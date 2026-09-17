"""Seeding the admin credential must survive an unwritable data root.

On a real container the data root is created by Docker as ``root:root``. hub-api used
to write the credential there *after* committing the admin row: the write failed, the
OSError escaped the lifespan handler, hub-api was restarted once by the supervisor, and
the second start found a user already present and skipped seeding for good. Every fresh
deployment therefore ended up with an admin account nobody had the password for.

These tests pin the two properties that keep it recoverable: an unwritable data root
must not leave a passwordless admin behind, and seeding must resume once the credential
can be written again.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import UserRecord
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
    )


def _usernames(client: TestClient) -> list[str]:
    factory = client.app.state.session_factory
    with factory() as session:
        return [row.username for row in session.query(UserRecord).all()]


@pytest.fixture
def deny_credential_write(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Fail only the credential write, the way a root-owned data root does on Linux."""
    real_write_text = Path.write_text

    def guarded(self: Path, *args: object, **kwargs: object) -> int:
        if self.name == "bootstrap-admin.json":
            raise PermissionError(13, "Permission denied")
        return int(real_write_text(self, *args, **kwargs))  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", guarded)
    yield monkeypatch


def test_unwritable_data_root_seeds_no_passwordless_admin(
    tmp_path: Path, deny_credential_write: pytest.MonkeyPatch
) -> None:
    """The account must not exist unless its credential was persisted first."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert _usernames(client) == []

    assert not (tmp_path / "data" / "bootstrap-admin.json").exists()


def test_seeding_resumes_once_the_credential_can_be_written(
    tmp_path: Path, deny_credential_write: pytest.MonkeyPatch
) -> None:
    """Fixing the permissions and restarting has to be enough to recover."""
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert _usernames(client) == []

    deny_credential_write.undo()

    with TestClient(create_app(_settings(tmp_path))) as client:
        assert _usernames(client) == ["admin"]

    credentials = json.loads((tmp_path / "data" / "bootstrap-admin.json").read_text("utf-8"))
    assert credentials["username"] == "admin"
    assert len(credentials["password"]) >= 20
