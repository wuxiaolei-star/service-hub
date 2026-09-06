"""File metadata service coordinated with safe local storage."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import FileRecord
from hub_server.storage import LocalStorage


class FileService:
    """Store upload contents and their metadata as one recoverable operation."""

    def __init__(self, session: Session, storage: LocalStorage, *, max_size_bytes: int) -> None:
        self._session = session
        self._storage = storage
        self._max_size_bytes = max_size_bytes

    def store_upload(
        self, filename: str, content_type: str | None, stream: BinaryIO
    ) -> FileRecord:
        """Install an upload first, then commit its database metadata."""
        file_key = f"file_{uuid4().hex}"
        stored = self._storage.store_upload(file_key, stream, max_size_bytes=self._max_size_bytes)
        safe_filename = Path(filename).name or "upload.bin"
        extension = Path(safe_filename).suffix.lower() or None
        record = FileRecord(
            file_key=file_key,
            scope="UPLOAD",
            role="INPUT",
            logical_name=safe_filename,
            original_filename=safe_filename,
            relative_path=stored.relative_path,
            extension=extension,
            mime_type=content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            status="AVAILABLE",
        )
        try:
            self._session.add(record)
            self._session.commit()
        except BaseException:
            try:
                self._session.rollback()
            finally:
                self._storage.remove_upload(file_key)
            raise
        return record

    def open_available(self, file_key: str) -> tuple[FileRecord, Path]:
        """Return an available record together with its safe on-disk payload path."""
        record = self._session.scalar(select(FileRecord).where(FileRecord.file_key == file_key))
        if record is None or record.status != "AVAILABLE":
            raise self._file_not_found(file_key)
        try:
            payload_path = self._storage.open_relative(record.relative_path)
        except ValueError as error:
            raise self._file_not_found(file_key) from error
        if not payload_path.is_file():
            raise self._file_not_found(file_key)
        return record, payload_path

    @staticmethod
    def _file_not_found(file_key: str) -> HubError:
        return HubError(
            code="FILE_NOT_FOUND",
            message=f"文件 {file_key} 不存在",
            status_code=404,
        )
