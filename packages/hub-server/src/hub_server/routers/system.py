"""System status endpoints."""

import sys

from fastapi import APIRouter, Request

from hub_server.schemas import HealthResponse, PlatformResponse, SystemInfoResponse
from hub_server.settings import HubSettings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(status="UP")


@router.get("/info", response_model=SystemInfoResponse)
def get_info(request: Request) -> SystemInfoResponse:
    settings: HubSettings = request.app.state.settings
    return SystemInfoResponse(
        hub_version=settings.hub_version,
        platform=PlatformResponse(os=settings.platform_os, arch=settings.platform_arch),
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}",
        deployment_mode=settings.deployment.mode,
    )
