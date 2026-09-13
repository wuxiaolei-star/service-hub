"""File upload, metadata, and download endpoints."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import MultipartParser, parse_options_header
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.responses import FileResponse as StreamingFileResponse

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import FileRecord
from hub_server.schemas import FileListResponse, FileResponse
from hub_server.services.audit import record as audit
from hub_server.services.files import FileService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage, StreamingUpload

router = APIRouter(
    prefix="/files", tags=["files"], dependencies=[Depends(require_role("viewer"))]
)

_MAX_MULTIPART_HEADER_COUNT = 8
_MAX_MULTIPART_HEADER_SIZE_BYTES = 4224


def _file_response(record: FileRecord) -> FileResponse:
    """Map persisted file metadata into the stable HTTP representation."""
    return FileResponse(
        file_id=record.file_key,
        name=record.logical_name,
        size=record.size_bytes,
        sha256=record.sha256,
        extension=record.extension,
        mime_type=record.mime_type,
        status="AVAILABLE",
        created_at=_utc_timestamp(record.created_at),
    )


def _utc_timestamp(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive values for the public UTC contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _file_service(session: Session, storage: LocalStorage, settings: HubSettings) -> FileService:
    """Construct the small service object used by each file route."""
    return FileService(session, storage, max_size_bytes=settings.uploads.max_size_bytes)


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
    record = await _stream_multipart_file(
        request, service, storage, settings.uploads.max_size_bytes
    )
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
    request: Request, service: FileService, storage: LocalStorage, max_size_bytes: int
) -> FileRecord:
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
        upload = storage.begin_upload(file_key, max_size_bytes=max_size_bytes)
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
            parser.write(chunk)
        parser.finalize()
        if not complete or upload is None or filename is None:
            raise _validation_error()
        stored = upload.finish()
        return service.store_installed_upload(file_key, filename, mime_type, stored)
    except Exception as error:
        if upload is not None:
            upload.abort()
        if isinstance(error, (FormParserError, UnicodeDecodeError)):
            raise _validation_error() from error
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
