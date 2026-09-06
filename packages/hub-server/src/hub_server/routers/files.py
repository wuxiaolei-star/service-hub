"""File upload, metadata, and download endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session
from starlette.responses import FileResponse as StreamingFileResponse

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.models import FileRecord
from hub_server.schemas import FileResponse
from hub_server.services.files import FileService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(prefix="/files", tags=["files"])


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
    )


def _file_service(session: Session, storage: LocalStorage, settings: HubSettings) -> FileService:
    """Construct the small service object used by each file route."""
    return FileService(session, storage, max_size_bytes=settings.uploads.max_size_bytes)


@router.post("", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
def upload_file(
    file: Annotated[UploadFile, File()],
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> FileResponse:
    """Stream one multipart upload into Hub-managed storage."""
    record = _file_service(session, storage, settings).store_upload(
        file.filename or "upload.bin", file.content_type, file.file
    )
    return _file_response(record)


@router.get("/{file_key}/download", response_class=StreamingFileResponse)
def download_file(
    file_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> StreamingFileResponse:
    """Download the available payload with a safe attachment filename."""
    record, payload_path = _file_service(session, storage, settings).open_available(file_key)
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
