"""File metadata service coordinated with safe local storage."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import FileRecord
from hub_server.storage import LocalStorage, StoredUpload

_LOGGER = logging.getLogger(__name__)


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
        file_key = self.new_file_key()
        stored = self._storage.store_upload(file_key, stream, max_size_bytes=self._max_size_bytes)
        return self.store_installed_upload(file_key, filename, content_type, stored)

    def new_file_key(self) -> str:
        """Create one unguessable external identifier for an in-progress upload."""
        return f"file_{uuid4().hex}"

    def store_installed_upload(
        self, file_key: str, filename: str, content_type: str | None, stored: StoredUpload
    ) -> FileRecord:
        """Commit metadata after a streaming upload atomically reaches storage."""
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
        except Exception as error:
            _LOGGER.exception("Unable to save Hub file metadata")
            try:
                self._session.rollback()
            except Exception:
                _LOGGER.exception("Unable to roll back Hub file metadata")
            finally:
                try:
                    self._storage.remove_upload(file_key)
                except Exception:
                    _LOGGER.exception("Unable to compensate failed Hub file metadata write")
            raise self._unexpected_error("保存文件元数据失败") from error
        return record

    def open_available(self, file_key: str) -> tuple[FileRecord, Path]:
        """Return an available record together with its safe on-disk payload path."""
        try:
            record = self._session.scalar(select(FileRecord).where(FileRecord.file_key == file_key))
        except Exception as error:
            _LOGGER.exception("Unable to read Hub file metadata")
            raise self._unexpected_error("读取文件元数据失败") from error
        if record is None or record.status != "AVAILABLE":
            raise self._file_not_found(file_key)
        try:
            payload_path = self._storage.open_relative(record.relative_path)
        except ValueError as error:
            raise self._file_not_found(file_key) from error
        try:
            is_regular_file = payload_path.is_file()
        except OSError as error:
            _LOGGER.exception("Unable to inspect Hub upload payload")
            raise self._unexpected_error("读取上传文件失败") from error
        if not is_regular_file:
            raise self._file_not_found(file_key)
        return record, payload_path

    @staticmethod
    def _file_not_found(file_key: str) -> HubError:
        return HubError(
            code="FILE_NOT_FOUND",
            message=f"文件 {file_key} 不存在",
            status_code=404,
        )

    @staticmethod
    def _unexpected_error(message: str) -> HubError:
        return HubError(code="UNEXPECTED_ERROR", message=message, status_code=500)
