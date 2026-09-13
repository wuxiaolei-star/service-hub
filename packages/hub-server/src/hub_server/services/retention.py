"""Retention sweeper for expired unreferenced input files."""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from hub_server.models import AuditLogRecord, FileRecord, JobFile
from hub_server.settings import RetentionSettings

SWEEP_BATCH_LIMIT = 500


class RetentionSweeper:
    """Delete expired, unreferenced input files in bounded batches."""

    def __init__(
        self,
        session: Session,
        storage_root: Path,
        settings: RetentionSettings,
    ) -> None:
        self._session = session
        self._storage_root = storage_root
        self._settings = settings

    def sweep(self, *, now: datetime | None = None) -> int:
        """Remove expired unreferenced inputs; return the number deleted."""
        current = now if now is not None else datetime.now(UTC)
        cutoff = current - timedelta(hours=self._settings.input_ttl_hours)
        candidates = (
            self._session.query(FileRecord)
            .outerjoin(JobFile, JobFile.file_record_id == FileRecord.id)
            .filter(
                FileRecord.role == "INPUT",
                FileRecord.created_at < cutoff,
                JobFile.id.is_(None),
            )
            .limit(SWEEP_BATCH_LIMIT)
            .all()
        )
        for record in candidates:
            payload = self._storage_root / record.relative_path
            with suppress(FileNotFoundError):
                payload.unlink()
            self._session.add(
                AuditLogRecord(
                    actor_type="system",
                    actor_id=None,
                    actor_name="cleaner",
                    action="file.retention_delete",
                    resource_type="file",
                    resource_id=record.file_key,
                    detail={"size_bytes": record.size_bytes},
                )
            )
            self._session.delete(record)
        if candidates:
            self._session.commit()
        return len(candidates)
