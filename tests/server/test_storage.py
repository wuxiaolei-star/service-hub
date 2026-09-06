"""Behavior tests for Hub-managed upload storage."""

import hashlib
import io
from pathlib import Path

import pytest
from hub_server.db import create_engine_and_session_factory
from hub_server.errors import HubError, UploadTooLargeError
from hub_server.models import Base, FileRecord
from hub_server.services.files import FileService
from hub_server.storage import LocalStorage


def test_store_upload_writes_payload_and_sha256(tmp_path: Path) -> None:
    """Changing streamed bytes or its destination must break stored upload integrity."""
    storage = LocalStorage(tmp_path)
    file_key = "file_123e4567e89b42d3a456426614174000"

    stored = storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)

    assert stored.relative_path == f"uploads/{file_key}/payload"
    assert (tmp_path / stored.relative_path).read_bytes() == b"netcdf"
    assert stored.size_bytes == 6
    assert stored.sha256 == hashlib.sha256(b"netcdf").hexdigest()


def test_store_upload_removes_partial_data_when_limit_exceeded(tmp_path: Path) -> None:
    """Accepting an over-limit stream or retaining its partial directory is a storage leak."""
    storage = LocalStorage(tmp_path)
    file_key = "file_123e4567e89b42d3a456426614174000"

    with pytest.raises(UploadTooLargeError) as error:
        storage.store_upload(file_key, io.BytesIO(b"01234567890"), max_size_bytes=10)

    assert error.value.details == {"max_size_bytes": 10}
    assert not (tmp_path / "uploads" / file_key).exists()
    assert not list((tmp_path / "uploads").glob(f"{file_key}.tmp-*"))


def test_store_upload_hides_payload_open_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filesystem write error must not expose the storage root to upload callers."""
    storage = LocalStorage(tmp_path)
    file_key = "file_123e4567e89b42d3a456426614174000"

    def fail_open(*_: object, **__: object) -> None:
        raise OSError(f"cannot write under {tmp_path}")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(HubError) as error:
        storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)

    assert error.value.code == "UNEXPECTED_ERROR"
    assert str(tmp_path) not in error.value.message
    assert isinstance(error.value.__cause__, OSError)


def test_store_upload_hides_atomic_move_and_cleanup_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed atomic move stays client-safe even when temporary cleanup also fails."""
    storage = LocalStorage(tmp_path)
    file_key = "file_123e4567e89b42d3a456426614174000"

    def fail_replace(*_: object, **__: object) -> None:
        raise OSError(f"cannot move under {tmp_path}")

    def fail_cleanup(*_: object, **__: object) -> None:
        raise OSError(f"cannot clean under {tmp_path}")

    monkeypatch.setattr("hub_server.storage.os.replace", fail_replace)
    monkeypatch.setattr("hub_server.storage.shutil.rmtree", fail_cleanup)

    with pytest.raises(HubError) as error:
        storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)

    assert error.value.code == "UNEXPECTED_ERROR"
    assert str(tmp_path) not in error.value.message
    assert isinstance(error.value.__cause__, OSError)


def test_remove_upload_hides_delete_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct storage cleanup failures must expose only a stable Hub error."""
    storage = LocalStorage(tmp_path)
    file_key = "file_123e4567e89b42d3a456426614174000"
    storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)

    def fail_cleanup(*_: object, **__: object) -> None:
        raise OSError(f"cannot clean under {tmp_path}")

    monkeypatch.setattr("hub_server.storage.shutil.rmtree", fail_cleanup)

    with pytest.raises(HubError) as error:
        storage.remove_upload(file_key)

    assert error.value.code == "UNEXPECTED_ERROR"
    assert str(tmp_path) not in error.value.message
    assert isinstance(error.value.__cause__, OSError)


def test_store_upload_rejects_file_keys_that_are_not_uuid4_hex(tmp_path: Path) -> None:
    """Permitting predictable or malformed file keys would defeat controlled storage names."""
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).store_upload("file_abcd", io.BytesIO(b"data"), max_size_bytes=10)


def test_open_relative_rejects_escape(tmp_path: Path) -> None:
    """A parent traversal must never resolve outside the Hub storage root."""
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).open_relative("../secret")


def test_open_relative_rejects_protocol_paths_over_1024_characters(tmp_path: Path) -> None:
    """Allowing oversized serialized paths violates the shared protocol path boundary."""
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).open_relative("a" * 1025)


def test_file_service_persists_sanitized_upload_metadata(tmp_path: Path) -> None:
    """A completed upload must have matching disk content and committed metadata."""
    storage = LocalStorage(tmp_path / "data")
    engine, session_factory = create_engine_and_session_factory("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with session_factory() as session:
            service = FileService(session, storage, max_size_bytes=10)
            record = service.store_upload(
                "../../MODEL.NC", "application/x-netcdf", io.BytesIO(b"netcdf")
            )

            assert record.file_key.startswith("file_")
            assert len(record.file_key) == len("file_") + 32
            assert record.logical_name == "MODEL.NC"
            assert record.original_filename == "MODEL.NC"
            assert record.extension == ".nc"
            assert record.mime_type == "application/x-netcdf"
            assert record.status == "AVAILABLE"
            assert session.get(type(record), record.id) is record
            opened_record, payload_path = service.open_available(record.file_key)
            assert opened_record is record
            assert payload_path.read_bytes() == b"netcdf"
    finally:
        engine.dispose()


def test_file_service_reports_unavailable_records_as_not_found(tmp_path: Path) -> None:
    """Exposing non-available metadata would allow consumers to access unusable files."""
    storage = LocalStorage(tmp_path / "data")
    engine, session_factory = create_engine_and_session_factory("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with session_factory() as session:
            service = FileService(session, storage, max_size_bytes=10)
            with pytest.raises(HubError) as error:
                service.open_available("file_missing")

            assert error.value.code == "FILE_NOT_FOUND"
            assert error.value.status_code == 404
            assert error.value.message == "文件 file_missing 不存在"
    finally:
        engine.dispose()


def test_file_service_hides_non_available_records(tmp_path: Path) -> None:
    """Returning a physical payload for an unavailable record bypasses its lifecycle state."""
    storage = LocalStorage(tmp_path / "data")
    file_key = "file_123e4567e89b42d3a456426614174000"
    stored = storage.store_upload(file_key, io.BytesIO(b"netcdf"), max_size_bytes=10)
    engine, session_factory = create_engine_and_session_factory("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with session_factory() as session:
            session.add(
                FileRecord(
                    file_key=file_key,
                    scope="UPLOAD",
                    role="INPUT",
                    logical_name="model.nc",
                    original_filename="model.nc",
                    relative_path=stored.relative_path,
                    extension=".nc",
                    size_bytes=stored.size_bytes,
                    sha256=stored.sha256,
                    status="DELETED",
                )
            )
            session.commit()
            service = FileService(session, storage, max_size_bytes=10)

            with pytest.raises(HubError) as error:
                service.open_available(file_key)

            assert error.value.code == "FILE_NOT_FOUND"
    finally:
        engine.dispose()


def test_file_service_removes_installed_file_when_metadata_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed metadata transaction must not leave an orphaned uploaded payload."""
    storage = LocalStorage(tmp_path / "data")
    engine, session_factory = create_engine_and_session_factory("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with session_factory() as session:
            service = FileService(session, storage, max_size_bytes=10)

            def fail_commit() -> None:
                raise RuntimeError("db down")

            monkeypatch.setattr(session, "commit", fail_commit)

            with pytest.raises(HubError) as error:
                service.store_upload("model.nc", None, io.BytesIO(b"netcdf"))

            assert error.value.code == "UNEXPECTED_ERROR"
            assert isinstance(error.value.__cause__, RuntimeError)
            assert not list((tmp_path / "data" / "uploads").iterdir())
    finally:
        engine.dispose()


def test_file_service_hides_database_error_when_rollback_and_cleanup_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback or deletion failures cannot mask the original metadata transaction error."""
    storage = LocalStorage(tmp_path / "data")
    engine, session_factory = create_engine_and_session_factory("sqlite://")
    Base.metadata.create_all(engine)
    commit_error = RuntimeError(f"database failed under {tmp_path}")
    rollback_called = False
    try:
        with session_factory() as session:
            service = FileService(session, storage, max_size_bytes=10)

            def fail_commit() -> None:
                raise commit_error

            def fail_rollback() -> None:
                nonlocal rollback_called
                rollback_called = True
                raise OSError(f"rollback failed under {tmp_path}")

            def fail_cleanup(*_: object, **__: object) -> None:
                raise OSError(f"cleanup failed under {tmp_path}")

            monkeypatch.setattr(session, "commit", fail_commit)
            monkeypatch.setattr(session, "rollback", fail_rollback)
            monkeypatch.setattr("hub_server.storage.shutil.rmtree", fail_cleanup)

            with pytest.raises(HubError) as error:
                service.store_upload("model.nc", None, io.BytesIO(b"netcdf"))

            assert rollback_called
            assert error.value.code == "UNEXPECTED_ERROR"
            assert str(tmp_path) not in error.value.message
            assert error.value.__cause__ is commit_error
    finally:
        engine.dispose()
