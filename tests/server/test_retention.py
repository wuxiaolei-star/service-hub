"""Retention sweeper rules for expired unreferenced input files."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from hub_server.models import FileRecord
from hub_server.services.retention import RetentionSweeper
from sqlalchemy.orm import Session


def _add_file(
    session: Session,
    *,
    file_key: str,
    role: str = "INPUT",
    age_hours: float,
) -> FileRecord:
    created_at = datetime.now(UTC) - timedelta(hours=age_hours)
    record = FileRecord(
        file_key=file_key,
        logical_name=f"{file_key}.nc",
        original_filename=f"{file_key}.nc",
        relative_path=f"files/{file_key}.nc",
        size_bytes=10,
        sha256="a" * 64,
        role=role,
        created_at=created_at,
    )
    session.add(record)
    session.commit()
    return record


def _payload(storage_root, record: FileRecord) -> Path:
    payload = storage_root / record.relative_path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"payload")
    return payload


def _sweeper(session: Session, storage_root, ttl_hours: float = 720) -> RetentionSweeper:
    from hub_server.settings import RetentionSettings

    return RetentionSweeper(
        session, storage_root, RetentionSettings(input_ttl_hours=int(ttl_hours))
    )


def test_expired_unreferenced_input_is_deleted(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_old", age_hours=800)
    payload = _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 1
    assert session.get(FileRecord, record.id) is None
    assert not payload.exists()


def test_recent_input_is_kept(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_new", age_hours=1)
    _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 0
    assert session.get(FileRecord, record.id) is not None


def test_output_files_are_never_deleted(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_out", role="OUTPUT", age_hours=9999)
    _payload(tmp_path, record)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 0
    assert session.get(FileRecord, record.id) is not None


def test_missing_payload_does_not_break_sweep(session: Session, tmp_path) -> None:
    _add_file(session, file_key="file_nofile", age_hours=9999)

    deleted = _sweeper(session, tmp_path).sweep()

    assert deleted == 1


def test_sweep_deletion_is_audited(session: Session, tmp_path) -> None:
    record = _add_file(session, file_key="file_aud", age_hours=9999)
    _payload(tmp_path, record)

    _sweeper(session, tmp_path).sweep()

    from hub_server.models import AuditLogRecord

    entry = session.query(AuditLogRecord).filter_by(action="file.retention_delete").one()
    assert entry.resource_id == record.file_key
    assert entry.actor_name == "cleaner"
