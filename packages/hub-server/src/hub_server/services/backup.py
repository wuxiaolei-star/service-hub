"""Crash-consistent SQLite backup archives for the Hub data directory.

Archives contain the database snapshot plus every top-level data directory of
the storage root (``files/``, ``plugins/``, ``jobs/``, ``uploads/``, ...);
the ``backups/``, ``environments/``, and ``logs/`` directories are excluded.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from hub_server.errors import HubError

_BACKUP_DIR_NAME = "backups"
_BACKUP_PATTERN = "backup-*.tar.gz"
_BACKUP_PREFIX = "backup-"
_BACKUP_SUFFIX = ".tar.gz"
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_DATABASE_ARCHIVE_NAME = "hub.db"
_EXCLUDED_DIRECTORY_NAMES = frozenset({"backups", "environments", "logs"})
# The archive holds a database snapshot plus a gzip copy of the data directories.
# Requiring 1.3x the estimated input size covers the snapshot copy, the growing
# archive itself, and compression working in our favour; it keeps a large backup
# from filling the very disk that stores the data it is protecting.
_SPACE_SAFETY_FACTOR = 1.3


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Metadata of one completed backup archive."""

    file_name: str
    size_bytes: int
    created_at: datetime


def resolve_db_path(database_url: str) -> Path:
    """Resolve the on-disk SQLite file path from the configured database URL."""
    parsed = make_url(database_url)
    database = parsed.database
    if not parsed.drivername.startswith("sqlite") or not database or database == ":memory:":
        raise HubError(
            code="BACKUP_UNSUPPORTED_DATABASE",
            message="备份仅支持文件型 SQLite 数据库",
            status_code=400,
        )
    return Path(database)


class BackupService:
    """Create deterministic tar.gz snapshots of the Hub data directory and prune them."""

    def __init__(self, *, db_path: Path, storage_root: Path, keep: int) -> None:
        self._db_path = db_path
        self._storage_root = storage_root
        self._keep = keep

    def create_backup(self, *, now: datetime | None = None) -> BackupResult:
        """Snapshot the database, archive it with the data directories, and prune."""
        current = now if now is not None else _utc_now()
        backup_dir = self._backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        self._require_free_disk_space()
        file_name = f"{_BACKUP_PREFIX}{current.strftime(_TIMESTAMP_FORMAT)}{_BACKUP_SUFFIX}"
        target = backup_dir / file_name
        partial = backup_dir / f".{file_name}.partial"
        with tempfile.TemporaryDirectory(prefix="hub-backup-") as staging:
            snapshot = Path(staging) / _DATABASE_ARCHIVE_NAME
            self._snapshot_database(snapshot)
            self._write_archive(partial, snapshot)
        os.replace(partial, target)
        self.prune_backups(self._keep)
        return BackupResult(
            file_name=file_name, size_bytes=target.stat().st_size, created_at=current
        )

    def _require_free_disk_space(self) -> None:
        """Refuse to start when the estimated archive could outgrow the disk.

        The estimate is the size of every file the archive will read (the data
        directories plus the database) plus room for the snapshot and the
        archive itself. Without this precheck a data directory that outgrew its
        disk would let the backup itself fill the drive and take the Hub down.
        """
        needed = int(self._estimate_archive_input_bytes() * _SPACE_SAFETY_FACTOR)
        free = shutil.disk_usage(self._storage_root).free
        if free < needed:
            raise HubError(
                code="DISK_SPACE_INSUFFICIENT",
                message="磁盘剩余空间不足以创建备份",
                status_code=409,
                details={"free": free, "needed": needed},
            )

    def _estimate_archive_input_bytes(self) -> int:
        """Approximate the bytes the archive will read, excluding never-archived directories."""
        total = 0
        if self._db_path.is_file():
            total += self._db_path.stat().st_size
        if not self._storage_root.is_dir():
            return total
        for path in self._storage_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self._storage_root)
            if relative.parts[0] in _EXCLUDED_DIRECTORY_NAMES:
                continue
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return total

    def prune_backups(self, keep: int) -> int:
        """Delete the oldest archives beyond ``keep``; return the number removed."""
        archives = sorted(self._backup_dir().glob(_BACKUP_PATTERN))
        stale = archives if keep <= 0 else archives[:-keep]
        removed = 0
        for path in stale:
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            removed += 1
        return removed

    def _backup_dir(self) -> Path:
        return self._storage_root / _BACKUP_DIR_NAME

    def _snapshot_database(self, snapshot: Path) -> None:
        """Copy the live SQLite database into a consistent snapshot via the backup API."""
        if not self._db_path.is_file():
            raise HubError(
                code="BACKUP_DATABASE_MISSING",
                message="数据库文件不存在或不可访问",
                status_code=409,
            )
        with (
            closing(sqlite3.connect(self._db_path)) as source,
            closing(sqlite3.connect(snapshot)) as destination,
        ):
            source.backup(destination)

    def _write_archive(self, target: Path, snapshot: Path) -> None:
        """Write the archive with deterministically ordered members."""
        members = sorted(self._archive_members(snapshot), key=lambda member: member[1])
        with tarfile.open(target, "w:gz") as archive:
            for path, arcname in members:
                archive.add(path, arcname=arcname, recursive=False)

    def _archive_members(self, snapshot: Path) -> list[tuple[Path, str]]:
        """Collect the database snapshot plus every retained top-level directory."""
        members = [(snapshot, _DATABASE_ARCHIVE_NAME)]
        if not self._storage_root.is_dir():
            return members
        for entry in sorted(self._storage_root.iterdir()):
            if not entry.is_dir() or entry.name in _EXCLUDED_DIRECTORY_NAMES:
                continue
            members.append((entry, entry.name))
            for path in sorted(entry.rglob("*")):
                members.append((path, path.relative_to(self._storage_root).as_posix()))
        return members
