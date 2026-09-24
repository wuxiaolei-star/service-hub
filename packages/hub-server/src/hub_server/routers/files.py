"""File upload, metadata, and download endpoints."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path as FsPath
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, Field
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse as StreamingFileResponse

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError, UploadTooLargeError
from hub_server.models import FileRecord, JobFile
from hub_server.schemas import FileListResponse, FileResponse
from hub_server.services.audit import record as audit
from hub_server.services.files import (
    PAYLOAD_BASENAME,
    FileService,
    chunk_file_name,
    chunk_staging_relative_path,
    new_chunk_upload_id,
)
from hub_server.services.quotas import QuotaService, UploadCap
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage, StoredUpload, StreamingUpload

_LOGGER = logging.getLogger(__name__)

router = APIRouter(
    prefix="/files", tags=["files"], dependencies=[Depends(require_role("viewer"))]
)

_MAX_MULTIPART_HEADER_COUNT = 8
_MAX_MULTIPART_HEADER_SIZE_BYTES = 4224
# The bytes of a multipart body that are not file content are bounded by the
# parser's own header limits, so a declared body larger than the file cap plus
# this slack cannot describe an acceptable upload. The slack only ever makes the
# declared-size rejection more conservative; the stream cap stays authoritative.
_MAX_MULTIPART_ENVELOPE_BYTES = 16 * 1024
# Chunked uploads merge at most this many parts, which also keeps the zero-padded
# on-disk chunk names a fixed width.
_MAX_CHUNK_COUNT = 10000
_CHUNK_COPY_BYTES = 1024 * 1024


class _StreamedUpload(NamedTuple):
    """One payload installed on disk, waiting for its metadata transaction."""

    file_key: str
    filename: str
    mime_type: str | None
    stored: StoredUpload


def _utc_timestamp(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive values for the public UTC contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _file_response(record: FileRecord) -> FileResponse:
    """Build the public file metadata response from a FileRecord."""
    return FileResponse(
        file_id=record.file_key,
        name=record.logical_name,
        size=record.size_bytes,
        sha256=record.sha256,
        extension=record.extension,
        mime_type=record.mime_type,
        status=record.status,  # type: ignore[arg-type]
        created_at=_utc_timestamp(record.created_at),
    )


def _file_service(session: Session, storage: LocalStorage, settings: HubSettings) -> FileService:
    """Construct the small service object used by each file route."""
    return FileService(session, storage, max_size_bytes=settings.uploads.max_size_bytes)


def _declared_content_length(request: Request) -> int | None:
    """Return the declared body size when the client sent a usable one.

    A chunked upload declares nothing, so the header is an optimisation only:
    the quota cap applied to the stream is what actually bounds the payload.
    """
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        declared = int(raw)
    except ValueError:
        return None
    return declared if declared >= 0 else None


def _discard_installed_upload(storage: LocalStorage, file_key: str) -> None:
    """Best-effort removal so a rejected upload keeps neither record nor payload."""
    try:
        storage.remove_upload(file_key)
    except HubError:
        _LOGGER.exception("Unable to remove the payload of a rejected upload")


@router.post(
    "",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
async def upload_file(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> FileResponse:
    """Parse one bounded multipart upload directly into Hub-managed storage."""
    service = _file_service(session, storage, settings)
    quota = QuotaService(session, settings.quotas)
    cap = quota.upload_cap(actor, upload_max_bytes=settings.uploads.max_size_bytes)
    if cap.max_bytes <= 0:
        raise cap.error()
    declared = _declared_content_length(request)
    if declared is not None and declared > cap.max_bytes + _MAX_MULTIPART_ENVELOPE_BYTES:
        raise cap.error()

    streamed = await _stream_multipart_file(request, service, storage, cap)
    try:
        record = service.install_upload_metadata(
            streamed.file_key,
            streamed.filename,
            streamed.mime_type,
            streamed.stored,
            owner_user_id=actor.id if actor.kind == "user" else None,
        )
        # Authoritative check. It runs after the row is flushed, so this
        # connection already holds the write lock, and before the commit, so a
        # concurrent upload cannot slip past it and a rejection leaves neither
        # metadata nor payload behind.
        quota.enforce_upload(actor, record.size_bytes, exclude_file_id=record.id)
        audit(
            session,
            actor_type=actor.kind,
            actor_id=actor.id,
            actor_name=actor.name,
            action="file.upload",
            resource_type="file",
            resource_id=record.file_key,
            ip=actor_ip(request),
        )
        session.commit()
    except Exception:
        session.rollback()
        _discard_installed_upload(storage, streamed.file_key)
        raise
    return _file_response(record)


@router.get("", response_model=FileListResponse)
def list_files(
    session: Annotated[Session, Depends(get_session)],
) -> FileListResponse:
    """Return the 100 most recently created public file records."""
    records = session.scalars(
        select(FileRecord)
        .where(FileRecord.status == "AVAILABLE")
        .order_by(FileRecord.created_at.desc())
        .limit(100)
    ).all()
    return FileListResponse(items=[_file_response(record) for record in records])


async def _stream_multipart_file(
    request: Request, service: FileService, storage: LocalStorage, cap: UploadCap
) -> _StreamedUpload:
    """Bound raw multipart bytes before they can enter a framework temporary file."""
    content_type, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or boundary is None:
        raise _validation_error()
    file_key = service.new_file_key()
    headers: dict[bytes, bytes] = {}
    header_name: list[bytes] = []
    header_value: list[bytes] = []
    upload: StreamingUpload | None = None
    current_is_file = False
    filename: str | None = None
    mime_type: str | None = None
    complete = False

    def on_part_begin() -> None:
        nonlocal current_is_file, headers
        current_is_file = False
        headers = {}

    def on_header_field(data: bytes, start: int, end: int) -> None:
        header_name.append(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        header_value.append(data[start:end])

    def on_header_end() -> None:
        headers[b"".join(header_name).lower()] = b"".join(header_value)
        header_name.clear()
        header_value.clear()

    def on_headers_finished() -> None:
        nonlocal current_is_file, filename, mime_type, upload
        disposition, disposition_options = parse_options_header(headers.get(b"content-disposition"))
        if disposition != b"form-data":
            raise _validation_error()
        field_name = disposition_options.get(b"name")
        if field_name != b"file":
            return
        file_name = disposition_options.get(b"filename")
        if file_name is None or upload is not None:
            raise _validation_error()
        filename = file_name.decode("latin-1")
        part_content_type = headers.get(b"content-type")
        mime_type = part_content_type.decode("latin-1") if part_content_type is not None else None
        upload = storage.begin_upload(file_key, max_size_bytes=cap.max_bytes)
        current_is_file = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if current_is_file:
            if upload is None:
                raise _validation_error()
            upload.write(data[start:end])

    def on_end() -> None:
        nonlocal complete
        complete = True

    try:
        parser = MultipartParser(
            boundary,
            callbacks={
                "on_part_begin": on_part_begin,
                "on_part_data": on_part_data,
                "on_header_field": on_header_field,
                "on_header_value": on_header_value,
                "on_header_end": on_header_end,
                "on_headers_finished": on_headers_finished,
                "on_end": on_end,
            },
            max_header_count=_MAX_MULTIPART_HEADER_COUNT,
            max_header_size=_MAX_MULTIPART_HEADER_SIZE_BYTES,
        )
    except FormParserError as error:
        raise _validation_error() from error
    try:
        async for chunk in request.stream():
            # The parser callbacks perform the payload write plus its digest
            # update; dispatching them off the event loop keeps a large upload
            # from blocking every other request for the whole transfer.
            await run_in_threadpool(parser.write, chunk)
        await run_in_threadpool(parser.finalize)
        if not complete or upload is None or filename is None:
            raise _validation_error()
        stored = await run_in_threadpool(upload.finish)
        return _StreamedUpload(file_key, filename, mime_type, stored)
    except Exception as error:
        if upload is not None:
            await run_in_threadpool(upload.abort)
        if isinstance(error, (FormParserError, UnicodeDecodeError)):
            raise _validation_error() from error
        # When the cap came from a quota rather than the upload limit, the
        # stream stopped early because the account ran out of room, not because
        # the file was too big for the Hub.
        if isinstance(error, UploadTooLargeError) and cap.quota is not None:
            raise cap.error() from error
        raise


def _validation_error() -> HubError:
    """Build one client-safe validation failure for malformed multipart input."""
    return HubError(code="REQUEST_VALIDATION_ERROR", message="请求参数无效", status_code=422)


@router.get(
    "/{file_key}/download",
    response_class=StreamingFileResponse,
    dependencies=[Depends(require_role("operator"))],
)
def download_file(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    file_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> StreamingFileResponse:
    """Download the available payload with a safe attachment filename."""
    record, payload_path = _file_service(session, storage, settings).open_available(file_key)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="file.download",
        resource_type="file",
        resource_id=record.file_key,
        ip=actor_ip(request),
    )
    return StreamingFileResponse(
        path=payload_path,
        media_type=record.mime_type or "application/octet-stream",
        filename=record.original_filename,
    )


@router.get("/{file_key}", response_model=FileResponse)
def get_file_metadata(
    file_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> FileResponse:
    """Return metadata for one available Hub file."""
    record, _ = _file_service(session, storage, settings).open_available(file_key)
    return _file_response(record)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@router.get(
    "/by-sha256/{sha256}",
    response_model=FileResponse,
    dependencies=[Depends(require_role("operator"))],
)
def get_file_by_sha256(
    sha256: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")],
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> FileResponse:
    """Return metadata for the newest available upload matching one checksum."""
    record = session.scalar(
        select(FileRecord)
        .where(FileRecord.status == "AVAILABLE", FileRecord.sha256 == sha256)
        .order_by(FileRecord.created_at.desc(), FileRecord.id.desc())
        .limit(1)
    )
    if record is None:
        raise _sha256_not_found(sha256)
    available, _ = _file_service(session, storage, settings).open_available(record.file_key)
    return _file_response(available)


def _sha256_not_found(sha256: str) -> HubError:
    return HubError(
        code="FILE_NOT_FOUND",
        message=f"文件 {sha256} 不存在",
        status_code=404,
    )


@router.delete(
    "/{file_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("operator"))],
)
def delete_file(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    file_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> Response:
    """Delete one available file that no Job still references."""
    record = session.scalar(select(FileRecord).where(FileRecord.file_key == file_key))
    if record is None or record.status != "AVAILABLE":
        raise _file_not_found(file_key)
    referenced = session.scalar(
        select(JobFile.id).where(JobFile.file_record_id == record.id).limit(1)
    )
    if referenced is not None:
        raise HubError(
            code="FILE_IN_USE",
            message="文件被 Job 引用，无法删除",  # noqa: RUF001
            status_code=409,
        )
    payload_path = storage.open_relative(record.relative_path)
    with contextlib.suppress(FileNotFoundError):
        payload_path.unlink()
    session.delete(record)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="file.delete",
        resource_type="file",
        resource_id=file_key,
        ip=actor_ip(request),
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _file_not_found(file_key: str) -> HubError:
    return HubError(
        code="FILE_NOT_FOUND",
        message=f"文件 {file_key} 不存在",
        status_code=404,
    )


class ChunkInitRequest(BaseModel):
    """Body for opening one chunked upload staging session."""

    filename: str = Field(min_length=1, max_length=512)
    total_size: int = Field(ge=0)


class ChunkInitResponse(BaseModel):
    upload_id: str


class ChunkCompleteRequest(BaseModel):
    """Body for merging staged chunks into one available Hub file."""

    filename: str = Field(min_length=1, max_length=512)
    total_chunks: int = Field(ge=1, le=_MAX_CHUNK_COUNT)


class ChunkUploadedResponse(BaseModel):
    chunk_index: int
    size: int


def _chunk_upload_not_found() -> HubError:
    return HubError(
        code="CHUNK_UPLOAD_NOT_FOUND",
        message="分片上传会话不存在",
        status_code=404,
    )


def _chunk_upload_incomplete() -> HubError:
    return HubError(
        code="CHUNK_UPLOAD_INCOMPLETE",
        message="分片不完整，无法合并",  # noqa: RUF001
        status_code=409,
    )


def _chunk_staging_directory(storage: LocalStorage, upload_id: str) -> FsPath:
    """Resolve one existing staging directory or raise the stable 404 error."""
    try:
        relative = chunk_staging_relative_path(upload_id)
    except ValueError as error:
        raise _chunk_upload_not_found() from error
    staging = storage.open_relative(relative)
    if not staging.is_dir():
        raise _chunk_upload_not_found()
    return staging


def _staged_chunk_bytes(staging: FsPath) -> int:
    """Return the bytes already staged, so one part cannot exceed the file cap."""
    total = 0
    for path in staging.glob("chunk_*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _merge_chunks(staging: FsPath, total_chunks: int, upload_id: str) -> StoredUpload:
    """Concatenate staged chunks in index order into the staging payload file."""
    digest = hashlib.sha256()
    size = 0
    try:
        with (staging / PAYLOAD_BASENAME).open("wb") as sink:
            for index in range(total_chunks):
                with (staging / chunk_file_name(index)).open("rb") as source:
                    while block := source.read(_CHUNK_COPY_BYTES):
                        digest.update(block)
                        size += len(block)
                        sink.write(block)
    except OSError as error:
        _LOGGER.exception("Unable to merge a chunked upload payload")
        raise HubError(
            code="UNEXPECTED_ERROR", message="保存上传文件失败", status_code=500
        ) from error
    return StoredUpload(
        relative_path=f"uploads/{upload_id}/payload",
        size_bytes=size,
        sha256=digest.hexdigest(),
    )


def _remove_chunk_files(staging: FsPath) -> None:
    """Best-effort removal of merged chunk files; the payload must survive."""
    try:
        for path in staging.glob("chunk_*"):
            if path.is_file():
                path.unlink(missing_ok=True)
    except OSError:
        _LOGGER.warning("Unable to clean merged chunk files", exc_info=True)


@router.post(
    "/chunk/init",
    response_model=ChunkInitResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def init_chunked_upload(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    body: ChunkInitRequest,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ChunkInitResponse:
    """Reserve a staging directory for one large multi-request upload."""
    if body.total_size > settings.uploads.max_size_bytes:
        raise UploadTooLargeError(max_size_bytes=settings.uploads.max_size_bytes)
    upload_id = new_chunk_upload_id()
    staging = storage.open_relative(chunk_staging_relative_path(upload_id))
    try:
        staging.mkdir(parents=True)
    except OSError as error:
        _LOGGER.exception("Unable to open a chunked upload staging directory")
        raise HubError(
            code="UNEXPECTED_ERROR", message="保存上传文件失败", status_code=500
        ) from error
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="file.chunk_init",
        resource_type="file",
        resource_id=upload_id,
        detail={"filename": body.filename, "total_size": body.total_size},
        ip=actor_ip(request),
    )
    try:
        session.commit()
    except Exception:
        session.rollback()
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ChunkInitResponse(upload_id=upload_id)


@router.put(
    "/chunk/{upload_id}/{chunk_index}",
    response_model=ChunkUploadedResponse,
    dependencies=[Depends(require_role("operator"))],
)
async def upload_chunk(
    upload_id: str,
    chunk_index: Annotated[int, Path(ge=0, le=_MAX_CHUNK_COUNT - 1)],
    request: Request,
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ChunkUploadedResponse:
    """Store one raw chunk below its staging directory, bounded by the file cap."""
    staging = await run_in_threadpool(_chunk_staging_directory, storage, upload_id)
    staged_bytes = await run_in_threadpool(_staged_chunk_bytes, staging)
    remaining = settings.uploads.max_size_bytes - staged_bytes
    target = staging / chunk_file_name(chunk_index)
    size = 0
    try:
        with target.open("wb") as sink:
            async for block in request.stream():
                size += len(block)
                if size > remaining:
                    raise UploadTooLargeError(max_size_bytes=settings.uploads.max_size_bytes)
                await run_in_threadpool(sink.write, block)
    except UploadTooLargeError:
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        raise
    except OSError as error:
        _LOGGER.exception("Unable to store a chunked upload part")
        raise HubError(
            code="UNEXPECTED_ERROR", message="保存上传文件失败", status_code=500
        ) from error
    return ChunkUploadedResponse(chunk_index=chunk_index, size=size)


@router.post(
    "/chunk/{upload_id}/complete",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def complete_chunked_upload(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    upload_id: str,
    body: ChunkCompleteRequest,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> FileResponse:
    """Merge staged chunks into one FileRecord under the usual upload quota."""
    staging = _chunk_staging_directory(storage, upload_id)
    if any(
        not (staging / chunk_file_name(index)).is_file() for index in range(body.total_chunks)
    ):
        raise _chunk_upload_incomplete()

    service = _file_service(session, storage, settings)
    stored = _merge_chunks(staging, body.total_chunks, upload_id)
    record = service.install_upload_metadata(
        service.new_file_key(),
        body.filename,
        None,
        stored,
        owner_user_id=actor.id if actor.kind == "user" else None,
    )
    try:
        # Authoritative quota check over the merged total. It runs after the row
        # is flushed, so this connection already holds the write lock, and before
        # the commit, so a rejection leaves neither metadata nor payload behind.
        QuotaService(session, settings.quotas).enforce_upload(
            actor, record.size_bytes, exclude_file_id=record.id
        )
        audit(
            session,
            actor_type=actor.kind,
            actor_id=actor.id,
            actor_name=actor.name,
            action="file.chunk_complete",
            resource_type="file",
            resource_id=record.file_key,
            detail={"upload_id": upload_id, "chunks": body.total_chunks},
            ip=actor_ip(request),
        )
        session.commit()
    except Exception:
        session.rollback()
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _remove_chunk_files(staging)
    return _file_response(record)
