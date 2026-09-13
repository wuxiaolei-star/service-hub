"""System status endpoints."""

import sys
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.schemas import HealthResponse, PlatformResponse, SystemInfoResponse
from hub_server.services.audit import record as audit
from hub_server.services.backup import BackupService, resolve_db_path
from hub_server.services.metrics import render_metrics
from hub_server.settings import HubSettings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(status="UP")


@router.get(
    "/metrics",
    dependencies=[Depends(require_role("admin"))],
)
def get_metrics(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Prometheus text exposition (admin only)."""
    settings = request.app.state.settings
    body = render_metrics(session, settings)
    return Response(content=body, media_type="text/plain; version=0.0.4")


@router.get(
    "/info",
    response_model=SystemInfoResponse,
    dependencies=[Depends(require_role("viewer"))],
)
def get_info(request: Request) -> SystemInfoResponse:
    settings: HubSettings = request.app.state.settings
    return SystemInfoResponse(
        hub_version=settings.hub_version,
        platform=PlatformResponse(os=settings.platform_os, arch=settings.platform_arch),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        deployment_mode=settings.deployment.mode,
    )


@router.post(
    "/backup",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
def create_backup(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Create a crash-consistent backup archive under the data directory (admin only)."""
    settings: HubSettings = request.app.state.settings
    service = BackupService(
        db_path=resolve_db_path(settings.database.url),
        storage_root=settings.storage.root,
        keep=settings.retention.backup_keep,
    )
    result = service.create_backup()
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="system.backup",
        resource_type="backup",
        resource_id=result.file_name,
        detail={"size_bytes": result.size_bytes},
        ip=actor_ip(request),
    )
    session.commit()
    return {
        "file_name": result.file_name,
        "size_bytes": result.size_bytes,
        "download_hint": (
            "通过 GET /api/v1/files/{id}/download 不适用；"  # noqa: RUF001
            "备份文件位于数据目录 backups/ 下"
        ),
    }
