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
_PLUGIN_BUILD_KEY_PATTERN = re.compile(r"^plugin_build_[0-9a-f]{32}$")
_STAGED_PLUGIN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_CHUNK_SIZE_BYTES = 1024 * 1024
_MAX_PROTOCOL_PATH_LENGTH = 1024
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """The Hub-relative location and integrity data of an installed upload."""

    relative_path: str
    size_bytes: int
    sha256: str


class StreamingUpload:
    """A single atomic upload installation accepting controlled byte chunks."""

    def __init__(
        self,
        *,
        file_key: str,
        temporary_directory: Path,
        destination_directory: Path,
        payload: BinaryIO,
        max_size_bytes: int,
    ) -> None:
        self._file_key = file_key
        self._temporary_directory = temporary_directory
        self._destination_directory = destination_directory
        self._payload: BinaryIO | None = payload
        self._max_size_bytes = max_size_bytes
        self._size_bytes = 0
        self._digest = hashlib.sha256()
        self._finished = False

    def write(self, chunk: bytes) -> None:
        """Persist one bounded parser chunk and update its digest."""
        if self._finished or self._payload is None:
            raise ValueError("upload stream is already closed")
        if self._size_bytes + len(chunk) > self._max_size_bytes:
            self.abort()
            raise UploadTooLargeError(max_size_bytes=self._max_size_bytes)
        try:
            written = self._payload.write(chunk)
            if written != len(chunk):
                raise OSError("short payload write")
        except OSError as error:
            self.abort()
            _LOGGER.exception("Unable to stream Hub upload")
            raise LocalStorage._storage_error("保存上传文件失败") from error
        self._size_bytes += len(chunk)
        self._digest.update(chunk)

    def finish(self) -> StoredUpload:
        """Atomically install the completed payload and return its integrity metadata."""
        if self._finished or self._payload is None:
            raise ValueError("upload stream is already closed")
        try:
            self._close_payload()
            os.replace(self._temporary_directory, self._destination_directory)
        except OSError as error:
            self.abort()
            _LOGGER.exception("Unable to install streamed Hub upload")
            raise LocalStorage._storage_error("保存上传文件失败") from error
        self._finished = True
        return StoredUpload(
            relative_path=f"uploads/{self._file_key}/payload",
            size_bytes=self._size_bytes,
            sha256=self._digest.hexdigest(),
        )

    def abort(self) -> None:
        """Close and discard an incomplete upload without exposing cleanup failures."""
        try:
            self._close_payload()
        except OSError:
            _LOGGER.exception("Unable to close incomplete Hub upload")
        LocalStorage._discard_temporary_directory(self._temporary_directory)

    def _close_payload(self) -> None:
        if self._payload is not None:
            payload = self._payload
            self._payload = None
            payload.close()


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
        upload = self.begin_upload(file_key, max_size_bytes=max_size_bytes)
        try:
            while chunk := stream.read(_CHUNK_SIZE_BYTES):
                upload.write(chunk)
            return upload.finish()
        except Exception:
            upload.abort()
            raise

    def begin_upload(self, file_key: str, *, max_size_bytes: int) -> StreamingUpload:
        """Open a Hub-controlled temporary payload for incremental writes."""
        temporary_directory: Path | None = None
        try:
            self._validate_file_key(file_key)
            uploads_directory = self._resolve_relative("uploads")
            uploads_directory.mkdir(parents=True, exist_ok=True)
            temporary_directory = self._resolve_relative(f"uploads/{file_key}.tmp-{uuid4().hex}")
            destination_directory = self._resolve_relative(f"uploads/{file_key}")
            if destination_directory.exists():
                raise ValueError("file key is already stored")
            temporary_directory.mkdir()
            payload = (temporary_directory / "payload").open("xb")
            return StreamingUpload(
                file_key=file_key,
                temporary_directory=temporary_directory,
                destination_directory=destination_directory,
                payload=payload,
                max_size_bytes=max_size_bytes,
            )
        except HubError:
            self._discard_temporary_directory(temporary_directory)
            raise
        except OSError as error:
            self._discard_temporary_directory(temporary_directory)
            _LOGGER.exception("Unable to begin Hub upload")
            raise self._storage_error("保存上传文件失败") from error

    def open_relative(self, relative_path: str) -> Path:
        """Resolve a protocol-relative path only when it remains under storage root."""
        try:
            return self._resolve_relative(relative_path)
        except OSError as error:
            _LOGGER.exception("Unable to resolve Hub storage path")
            raise self._storage_error("读取文件存储失败") from error

    def relative_path(self, path: Path) -> str:
        """Return a POSIX path only for an existing or prospective child of storage."""
        try:
            relative = path.resolve().relative_to(self._root)
        except (OSError, ValueError) as error:
            raise ValueError("path must stay within Hub storage") from error
        if not relative.parts:
            raise ValueError("path must identify a child of Hub storage")
        return relative.as_posix()

    def create_temporary_directory(self, parent_relative_path: str) -> Path:
        """Create one private UUID-scoped directory below a controlled parent."""
        temporary_directory: Path | None = None
        try:
            parent = self._resolve_relative(parent_relative_path)
            parent.mkdir(parents=True, exist_ok=True)
            temporary_directory = parent / f".tmp-{uuid4().hex}"
            temporary_directory.mkdir()
            return temporary_directory
        except OSError as error:
            self._discard_temporary_directory(temporary_directory)
            _LOGGER.exception("Unable to create Hub temporary directory")
            raise self._storage_error("无法创建临时存储目录") from error

    def install_directory(
        self, temporary_directory: Path, destination_relative_path: str
    ) -> Path:
        """Atomically rename one controlled temporary directory to a safe destination."""
        destination = self._resolve_relative(destination_relative_path)
        try:
            temporary = temporary_directory.resolve()
            temporary.relative_to(self._root)
            if not temporary.name.startswith(".tmp-"):
                raise ValueError("source must be a Hub temporary directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise ValueError("storage destination already exists")
            os.replace(temporary, destination)
            return destination
        except ValueError:
            raise
        except OSError as error:
            _LOGGER.exception("Unable to install Hub directory")
            raise self._storage_error("无法安装存储目录") from error

    def install_staged_plugin(self, staging_directory: Path, build_key: str) -> Path:
        """Atomically promote one verified UUID staging directory to its Build directory."""
        if _PLUGIN_BUILD_KEY_PATTERN.fullmatch(build_key) is None:
            raise ValueError("plugin build key is invalid")
        staging = staging_directory.resolve()
        staging_root = self._resolve_relative("plugins/.staging")
        try:
            staging.relative_to(staging_root)
        except ValueError as error:
            raise ValueError("source must be a verified plugin staging directory") from error
        if (
            staging.parent != staging_root
            or _STAGED_PLUGIN_PATTERN.fullmatch(staging.name) is None
        ):
            raise ValueError("source must be a verified plugin staging directory")
        destination = self._resolve_relative(f"plugins/{build_key}")
        try:
            if destination.exists():
                raise ValueError("plugin build storage already exists")
            os.replace(staging, destination)
            return destination
        except ValueError:
            raise
        except OSError as error:
            _LOGGER.exception("Unable to install verified plugin Build")
            raise self._storage_error("无法安装插件 Build") from error

    def discard_staged_plugin(self, staging_directory: Path) -> None:
        """Remove one UUID-scoped verified package that was not installed."""
        staging = staging_directory.resolve()
        staging_root = self._resolve_relative("plugins/.staging")
        if (
            staging.parent != staging_root
            or _STAGED_PLUGIN_PATTERN.fullmatch(staging.name) is None
        ):
            _LOGGER.warning("Refusing to discard a non-staging plugin directory")
            return
        self._discard_temporary_directory(staging)

    def remove_plugin_build(self, build_key: str) -> None:
        """Remove one exact Build directory after its metadata update failed."""
        if _PLUGIN_BUILD_KEY_PATTERN.fullmatch(build_key) is None:
            raise ValueError("plugin build key is invalid")
        directory = self._resolve_relative(f"plugins/{build_key}")
        try:
            if directory.exists():
                shutil.rmtree(directory)
        except OSError as error:
            _LOGGER.exception("Unable to remove failed plugin Build storage")
            raise self._storage_error("无法清理插件 Build") from error

    def discard_temporary_directory(self, temporary_directory: Path | None) -> None:
        """Discard one temporary directory created by this storage instance."""
        if temporary_directory is None:
            return
        try:
            temporary = temporary_directory.resolve()
            temporary.relative_to(self._root)
        except (OSError, ValueError):
            _LOGGER.warning("Refusing to discard a directory outside Hub storage")
            return
        if not temporary.name.startswith(".tmp-"):
            _LOGGER.warning("Refusing to discard a non-temporary Hub directory")
            return
        self._discard_temporary_directory(temporary)

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
