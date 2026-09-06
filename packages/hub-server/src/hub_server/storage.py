"""Safe local persistence for large Hub-managed upload payloads."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID, uuid4

from hub_server.errors import HubError, UploadTooLargeError

_FILE_KEY_PATTERN = re.compile(r"^file_([0-9a-f]{32})$")
_CHUNK_SIZE_BYTES = 1024 * 1024
_MAX_PROTOCOL_PATH_LENGTH = 1024
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """The Hub-relative location and integrity data of an installed upload."""

    relative_path: str
    size_bytes: int
    sha256: str


class LocalStorage:
    """Persist and resolve payloads below one Hub-controlled directory tree."""

    def __init__(self, root: Path) -> None:
        try:
            root.mkdir(parents=True, exist_ok=True)
            self._root = root.resolve()
        except OSError as error:
            _LOGGER.exception("Unable to initialize Hub storage")
            raise self._storage_error("无法初始化文件存储") from error

    def store_upload(
        self, file_key: str, stream: BinaryIO, *, max_size_bytes: int
    ) -> StoredUpload:
        """Stream an upload into a temporary directory then atomically install it."""
        temporary_directory: Path | None = None
        size_bytes = 0
        digest = hashlib.sha256()
        try:
            self._validate_file_key(file_key)
            uploads_directory = self._resolve_relative("uploads")
            uploads_directory.mkdir(parents=True, exist_ok=True)
            temporary_directory = self._resolve_relative(f"uploads/{file_key}.tmp-{uuid4().hex}")
            destination_directory = self._resolve_relative(f"uploads/{file_key}")
            if destination_directory.exists():
                raise ValueError("file key is already stored")

            temporary_directory.mkdir()
            payload_path = temporary_directory / "payload"
            with payload_path.open("xb") as payload:
                while chunk := stream.read(_CHUNK_SIZE_BYTES):
                    size_bytes += len(chunk)
                    if size_bytes > max_size_bytes:
                        raise UploadTooLargeError(max_size_bytes=max_size_bytes)
                    digest.update(chunk)
                    payload.write(chunk)
            os.replace(temporary_directory, destination_directory)
        except UploadTooLargeError:
            self._discard_temporary_directory(temporary_directory)
            raise
        except HubError:
            self._discard_temporary_directory(temporary_directory)
            raise
        except OSError as error:
            self._discard_temporary_directory(temporary_directory)
            _LOGGER.exception("Unable to store Hub upload")
            raise self._storage_error("保存上传文件失败") from error

        relative_path = f"uploads/{file_key}/payload"
        return StoredUpload(
            relative_path=relative_path,
            size_bytes=size_bytes,
            sha256=digest.hexdigest(),
        )

    def open_relative(self, relative_path: str) -> Path:
        """Resolve a protocol-relative path only when it remains under storage root."""
        try:
            return self._resolve_relative(relative_path)
        except OSError as error:
            _LOGGER.exception("Unable to resolve Hub storage path")
            raise self._storage_error("读取文件存储失败") from error

    def remove_upload(self, file_key: str) -> None:
        """Remove one known upload directory after a failed metadata transaction."""
        self._validate_file_key(file_key)
        try:
            upload_directory = self._resolve_relative(f"uploads/{file_key}")
            if upload_directory.exists():
                shutil.rmtree(upload_directory)
        except OSError as error:
            _LOGGER.exception("Unable to remove Hub upload")
            raise self._storage_error("删除上传文件失败") from error

    @staticmethod
    def _storage_error(message: str) -> HubError:
        return HubError(code="UNEXPECTED_ERROR", message=message, status_code=500)

    @staticmethod
    def _discard_temporary_directory(temporary_directory: Path | None) -> None:
        if temporary_directory is None:
            return
        try:
            shutil.rmtree(temporary_directory, ignore_errors=True)
        except OSError:
            _LOGGER.exception("Unable to discard temporary Hub upload")

    def _resolve_relative(self, relative_path: str) -> Path:
        normalized = self._normalize_relative_path(relative_path)
        candidate = (self._root / PurePosixPath(normalized)).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as error:
            raise ValueError("path must stay within Hub storage") from error
        return candidate

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> str:
        if not isinstance(relative_path, str):
            raise ValueError("path must be a safe relative protocol path")
        normalized = relative_path.replace("\\", "/")
        if (
            not normalized
            or "\x00" in normalized
            or len(normalized) > _MAX_PROTOCOL_PATH_LENGTH
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise ValueError("path must be a safe relative protocol path")
        segments = normalized.split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise ValueError("path must not contain empty, current, or parent segments")
        return normalized

    @staticmethod
    def _validate_file_key(file_key: str) -> None:
        match = _FILE_KEY_PATTERN.fullmatch(file_key)
        if match is None:
            raise ValueError("file key must be a UUID4-based Hub file key")
        try:
            parsed = UUID(hex=match.group(1))
        except ValueError as error:
            raise ValueError("file key must be a UUID4-based Hub file key") from error
        if parsed.version != 4:
            raise ValueError("file key must be a UUID4-based Hub file key")
