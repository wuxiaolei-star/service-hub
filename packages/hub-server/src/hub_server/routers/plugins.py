"""Public Plugin and Plugin Build lifecycle endpoints."""

from __future__ import annotations

import hashlib
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, UploadFile, status
from python_hub_contracts import RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.errors import HubError
from hub_server.models import Job, Plugin, PluginBuild
from hub_server.schemas import PluginBuildResponse, PluginListResponse, PluginSummary
from hub_server.services.archives import PluginArchiveService
from hub_server.services.plugins import PluginService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(tags=["plugins"])


@router.post(
    "/plugins/install",
    response_model=PluginBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_plugin(
    package: Annotated[UploadFile, File(alias="file")],
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> PluginBuildResponse:
    """Verify and register one uploaded `.pypkg` Build package."""
    package_sha256 = _sha256_upload(package)
    verified = PluginArchiveService(settings.storage.root).verify_and_install(
        package.file,
        package_sha256,
    )
    build = PluginService(
        session,
        storage,
        platform_os=settings.platform_os,
        platform_arch=settings.platform_arch,
    ).stage_installation(verified, package_sha256=package_sha256)
    return _build_response(build)


@router.get("/plugins", response_model=PluginListResponse)
def list_plugins(
    session: Annotated[Session, Depends(get_session)],
) -> PluginListResponse:
    """Return installed plugin summaries."""
    plugins = session.scalars(select(Plugin).order_by(Plugin.plugin_key)).all()
    items = [
        PluginSummary(
            id=plugin.plugin_key,
            name=plugin.name,
            description=plugin.description,
            category=plugin.category,
            latest_version=max((version.version for version in plugin.versions), default=None),
        )
        for plugin in plugins
    ]
    return PluginListResponse(items=items)


@router.get("/plugin-builds/{build_key}", response_model=PluginBuildResponse)
def get_build(
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginBuildResponse:
    """Return one Plugin Build summary."""
    return _build_response(_find_build(session, build_key))


@router.post("/plugin-builds/{build_key}/enable", response_model=PluginBuildResponse)
def enable_build(
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginBuildResponse:
    """Enable one READY Build explicitly."""
    build = _find_build(session, build_key)
    if build.status != "READY" and build.status != "ENABLED":
        raise HubError(
            code="PLUGIN_BUILD_NOT_READY",
            message="插件 Build 尚未 READY, 不能启用",
            status_code=409,
        )
    build.status = "ENABLED"
    session.commit()
    session.refresh(build)
    return _build_response(build)


@router.post("/plugin-builds/{build_key}/disable", response_model=PluginBuildResponse)
def disable_build(
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginBuildResponse:
    """Disable a Build when it has no active Jobs."""
    build = _find_build(session, build_key)
    active = session.scalar(
        select(Job).where(
            Job.plugin_build_id == build.id,
            Job.status.in_(["PREPARING", "RUNNING"]),
        )
    )
    if active is not None:
        raise HubError(
            code="PLUGIN_BUILD_IN_USE",
            message="插件 Build 正在被 Job 使用, 不能禁用",
            status_code=409,
        )
    if build.status == "ENABLED":
        build.status = "READY"
        session.commit()
        session.refresh(build)
    return _build_response(build)


def _find_build(session: Session, build_key: str) -> PluginBuild:
    build = session.scalar(select(PluginBuild).where(PluginBuild.build_key == build_key))
    if build is None:
        raise HubError(
            code="PLUGIN_BUILD_NOT_FOUND",
            message="插件 Build 不存在",
            status_code=404,
        )
    return build


def _build_response(build: PluginBuild) -> PluginBuildResponse:
    return PluginBuildResponse(
        build_id=build.build_key,
        plugin_id=build.plugin_version.plugin.plugin_key,
        version=build.plugin_version.version,
        runtime_type=cast(RuntimeType, build.runtime_type),
        target_os=build.target_os,
        target_arch=build.target_arch,
        status=build.status,
        package_sha256=build.package_sha256,
        runtime_fingerprint=build.runtime_fingerprint,
        error_summary=build.error_summary,
    )


def _sha256_upload(package: UploadFile) -> str:
    digest = hashlib.sha256()
    while chunk := package.file.read(1024 * 1024):
        digest.update(chunk)
    package.file.seek(0)
    return digest.hexdigest()
