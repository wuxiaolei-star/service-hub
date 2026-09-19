"""Retention sweeper for expired unreferenced input files."""

from __future__ import annotations

import shutil
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from python_hub_contracts import JobStatus
from sqlalchemy.orm import Session

from hub_server.models import AuditLogRecord, FileRecord, Job, JobCallback, JobFile, SessionRecord
from hub_server.services.audit import record as audit
from hub_server.services.files import CHUNK_STAGING_ID_PATTERN, PAYLOAD_BASENAME
from hub_server.settings import RetentionSettings

SWEEP_BATCH_LIMIT = 500
# A chunked-upload staging session that has not completed within one day is
# abandoned; its staged parts are temporary data no FileRecord ever references.
_CHUNK_STAGING_MAX_AGE_HOURS = 24

TERMINAL_JOB_STATUSES = (
    JobStatus.SUCCESS.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.TIMED_OUT.value,
)


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
        """Remove expired unreferenced inputs and abandoned chunk staging.

        Returns the number of deleted records plus removed staging directories.
        """
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
        removed_directories = sweep_stale_chunk_directories(self._storage_root, now=current)
        return len(candidates) + removed_directories


def sweep_stale_chunk_directories(storage_root: Path, *, now: datetime | None = None) -> int:
    """Remove chunked-upload staging directories idle for over 24 hours; return the count.

    Staging directories are named by their raw 32-hex upload id, so the
    ``file_``-prefixed directories of installed uploads never match. A staging
    directory that already carries a merged ``payload`` belongs to a completed
    upload whose FileRecord references it and is never touched here.
    """
    current = now if now is not None else datetime.now(UTC)
    cutoff = (current - timedelta(hours=_CHUNK_STAGING_MAX_AGE_HOURS)).timestamp()
    try:
        entries = list((storage_root / "uploads").iterdir())
    except OSError:
        return 0
    removed = 0
    for entry in entries:
        if not entry.is_dir() or CHUNK_STAGING_ID_PATTERN.fullmatch(entry.name) is None:
            continue
        if (entry / PAYLOAD_BASENAME).exists():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    return removed


def sweep_terminal_jobs(
    session: Session,
    storage_root: Path,
    settings: RetentionSettings,
    now: datetime | None = None,
) -> int:
    """Delete terminal Jobs older than ``job_retention_days``; return the count.

    Each batch removes at most ``SWEEP_BATCH_LIMIT`` Jobs together with their
    workspace directory and OUTPUT file payloads. INPUT file snapshots stay
    behind: they belong to their uploading users and age out through the
    unreferenced-input sweep above. Job rows go first so the CASCADE clears
    ``job_files`` and releases the RESTRICT guard before OUTPUT FileRecords go;
    everything happens inside one transaction that only commits when something
    was actually deleted.
    """
    current = now if now is not None else datetime.now(UTC)
    cutoff = current - timedelta(days=settings.job_retention_days)
    candidates = (
        session.query(Job.id, Job.job_key, Job.workspace_path)
        .filter(Job.status.in_(TERMINAL_JOB_STATUSES), Job.updated_at < cutoff)
        .limit(SWEEP_BATCH_LIMIT)
        .all()
    )
    for job_id, job_key, workspace_path in candidates:
        outputs = (
            session.query(FileRecord.id, FileRecord.relative_path)
            .join(JobFile, JobFile.file_record_id == FileRecord.id)
            .filter(JobFile.job_id == job_id, JobFile.role == "OUTPUT")
            .all()
        )
        if workspace_path:
            shutil.rmtree(storage_root / workspace_path, ignore_errors=True)
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="cleaner",
            action="job.retention_delete",
            resource_type="job",
            resource_id=job_key,
            detail={"workspace": workspace_path},
        )
        session.query(JobFile).filter(JobFile.job_id == job_id).delete(
            synchronize_session=False
        )
        session.query(JobCallback).filter(JobCallback.job_id == job_id).delete(
            synchronize_session=False
        )
        session.query(Job).filter(Job.id == job_id).delete(synchronize_session=False)
        for _, relative_path in outputs:
            with suppress(FileNotFoundError):
                (storage_root / relative_path).unlink()
        if outputs:
            session.query(FileRecord).filter(
                FileRecord.id.in_([record_id for record_id, _ in outputs])
            ).delete(synchronize_session=False)
    if candidates:
        session.commit()
    return len(candidates)


def sweep_expired_sessions(session: Session, now: datetime | None = None) -> int:
    """Delete sessions whose ``expires_at`` has passed; return the count.

    One summary audit entry covers the whole batch instead of one entry per row.
    """
    current = now if now is not None else datetime.now(UTC)
    expired = (
        session.query(SessionRecord)
        .filter(SessionRecord.expires_at < current)
        .limit(SWEEP_BATCH_LIMIT)
        .all()
    )
    for record in expired:
        session.delete(record)
    if expired:
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="cleaner",
            action="session.sweep",
            resource_type="session",
            detail={"deleted": len(expired)},
        )
        session.commit()
    return len(expired)


def sweep_old_audit_logs(
    session: Session, settings: RetentionSettings, now: datetime | None = None
) -> int:
    """Delete audit log entries older than ``audit_retention_days``; return the count.

    One summary audit entry covers the whole batch instead of one entry per row.
    """
    current = now if now is not None else datetime.now(UTC)
    cutoff = current - timedelta(days=settings.audit_retention_days)
    stale = (
        session.query(AuditLogRecord)
        .filter(AuditLogRecord.at < cutoff)
        .limit(SWEEP_BATCH_LIMIT)
        .all()
    )
    for record in stale:
        session.delete(record)
    if stale:
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="cleaner",
            action="audit.sweep",
            resource_type="audit_log",
            detail={"deleted": len(stale)},
        )
        session.commit()
    return len(stale)
