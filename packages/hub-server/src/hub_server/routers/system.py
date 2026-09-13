"""System status endpoints."""

import sys
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import require_role
from hub_server.schemas import HealthResponse, PlatformResponse, SystemInfoResponse
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
