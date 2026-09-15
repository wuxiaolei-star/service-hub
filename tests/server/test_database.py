"""Integration tests for SQLite metadata persistence."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.db import create_engine_and_session_factory
from hub_server.main import create_app
from hub_server.models import FileRecord
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def settings(tmp_path: Path) -> HubSettings:
    """Use a per-test SQLite database without relying on host deployment paths."""
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
    auth=AuthSettings(mode="off"),
    )


@pytest.fixture
def session(settings: HubSettings) -> Iterator[Session]:
    """Provide a session backed by the application's migrated SQLite database."""
    app = create_app(settings)
    with TestClient(app):
        session_factory = app.state.session_factory
        with session_factory() as database_session:
            yield database_session


def test_startup_creates_file_records_table(settings: HubSettings) -> None:
    """Startup migrations provision file metadata before serving requests."""
    app = create_app(settings)

    with TestClient(app):
        assert "file_records" in inspect(app.state.engine).get_table_names()


def test_startup_uses_explicit_settings_database_when_environment_conflicts(
    settings: HubSettings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """App lifespan migrations target its explicit settings instead of a process environment URL."""
    environment_database = tmp_path / "environment" / "hub.db"
    environment_database.parent.mkdir()
    monkeypatch.setenv("HUB_DATABASE_URL", f"sqlite:///{environment_database.as_posix()}")
    app = create_app(settings)

    with TestClient(app):
        assert "file_records" in inspect(app.state.engine).get_table_names()

    assert not environment_database.exists()


def test_startup_creates_missing_database_parent_directory(
    settings: HubSettings, tmp_path: Path
) -> None:
    """A configured SQLite file can live below a newly provisioned data directory."""
    database_path = tmp_path / "data" / "db" / "hub.db"
    nested_settings = settings.model_copy(
        update={"database": DatabaseSettings(url=f"sqlite:///{database_path.as_posix()}")}
    )

    with TestClient(create_app(nested_settings)):
        assert database_path.is_file()


def test_sqlite_engine_enables_foreign_keys(tmp_path: Path) -> None:
    """SQLite connections enforce foreign-key constraints for future related metadata."""
    engine, _ = create_engine_and_session_factory(
        f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"
    )
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    finally:
        engine.dispose()


def test_sqlite_engine_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    """Concurrent Hub processes share the database without lock failures.

    WAL lets the cleaner, scheduler, and runners write while hub-api reads,
    and the busy timeout makes competing writers wait instead of raising
    "database is locked" on the first collision.
    """
    engine, _ = create_engine_and_session_factory(
        f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"
    )
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30000
    finally:
        engine.dispose()


def test_file_record_rejects_duplicate_file_keys(session: Session) -> None:
    """A duplicate external file key is rejected instead of corrupting lookup state."""
    common = {
        "scope": "UPLOAD",
        "role": "INPUT",
        "logical_name": "model.nc",
        "original_filename": "model.nc",
        "extension": ".nc",
        "size_bytes": 1,
        "sha256": "a" * 64,
        "status": "AVAILABLE",
        "created_at": datetime.now(UTC),
    }
    session.add_all(
        [
            FileRecord(file_key="file_same", relative_path="uploads/a/payload", **common),
            FileRecord(file_key="file_same", relative_path="uploads/b/payload", **common),
        ]
    )

    with pytest.raises(IntegrityError):
        session.commit()
