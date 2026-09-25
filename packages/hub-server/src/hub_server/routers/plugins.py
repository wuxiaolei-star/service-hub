"""Public Plugin and Plugin Build lifecycle endpoints."""

from __future__ import annotations

import hashlib
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from python_hub_contracts import RuntimeType
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session, selectinload

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion
from hub_server.schemas import (
    PluginBuildListResponse,
    PluginBuildResponse,
    PluginDetailResponse,
    PluginListResponse,
    PluginSummary,
    PluginVersionDetail,
)
from hub_server.services.archives import PluginArchiveService
from hub_server.services.audit import record as audit
from hub_server.services.plugins import PluginService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(tags=["plugins"], dependencies=[Depends(require_role("viewer"))])


@router.post(
    "/plugins/install",
    response_model=PluginBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_role("publisher"))],
)
async def install_plugin(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
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
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="plugin.install",
        resource_type="plugin_build",
        resource_id=verified.build.build_id,
        detail={"package_sha256": package_sha256},
        ip=actor_ip(request),
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
    plugins = session.scalars(
        select(Plugin).options(selectinload(Plugin.versions)).order_by(Plugin.plugin_key)
    ).all()
    items = [_plugin_summary(plugin) for plugin in plugins]
    return PluginListResponse(items=items)


@router.get("/plugins/{plugin_id}", response_model=PluginDetailResponse)
def get_plugin_detail(
    plugin_id: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginDetailResponse:
    """Return one installed plugin and the public manifests of its versions."""
    plugin = session.scalar(
        select(Plugin)
        .where(Plugin.plugin_key == plugin_id)
        .options(selectinload(Plugin.versions).selectinload(PluginVersion.builds))
    )
    if plugin is None:
        raise HubError(code="PLUGIN_NOT_FOUND", message="插件不存在", status_code=404)
    summary = _plugin_summary(plugin)
    return PluginDetailResponse(
        **summary.model_dump(),
        author=plugin.author,
        versions=[
            PluginVersionDetail(
                version=version.version,
                spec_version=version.spec_version,
                sdk_version=version.sdk_version,
                source_sha256=version.source_sha256,
                status=version.status,
                manifest=version.manifest_json,
            )
            for version in sorted(
                plugin.versions, key=lambda version: version.version, reverse=True
            )
        ],
    )


@router.get("/plugin-builds", response_model=PluginBuildListResponse)
def list_plugin_builds(
    session: Annotated[Session, Depends(get_session)],
    plugin_id: Annotated[str | None, Query(max_length=64)] = None,
    runtime_type: Annotated[RuntimeType | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PluginBuildListResponse:
    """Return a page of plugin Builds newest-first plus the total matching the filters.

    The default limit stays at 100 (not the 50 used by /jobs) on purpose: this
    endpoint predates pagination and returned up to 100 Builds, so parameterless
    callers keep seeing the same newest-100 window.
    """
    conditions: list[ColumnElement[bool]] = []
    query = (
        select(PluginBuild)
        .join(PluginBuild.plugin_version)
        .join(PluginVersion.plugin)
        .options(selectinload(PluginBuild.plugin_version).selectinload(PluginVersion.plugin))
    )
    if plugin_id is not None:
        conditions.append(Plugin.plugin_key == plugin_id)
    if runtime_type is not None:
        conditions.append(PluginBuild.runtime_type == runtime_type)
    if conditions:
        query = query.where(*conditions)
    # The total counts every Build matching the filters, independent of the page
    # window (same pattern as JobService.list_jobs).
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    builds = session.scalars(
        query.order_by(PluginBuild.created_at.desc(), PluginBuild.build_key.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return PluginBuildListResponse(
        items=[_build_response(build) for build in builds],
        total=int(total or 0),
    )


@router.get("/plugin-builds/{build_key}", response_model=PluginBuildResponse)
def get_build(
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginBuildResponse:
    """Return one Plugin Build summary."""
    return _build_response(_find_build(session, build_key))


@router.post(
    "/plugin-builds/{build_key}/deprecate",
    response_model=PluginBuildResponse,
    dependencies=[Depends(require_role("publisher"))],
)
def deprecate_build(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
) -> PluginBuildResponse:
    """Deprecate one READY or ENABLED Build; the transition is one-way.

    A DEPRECATED Build can no longer back new Jobs (job resolution only accepts
    ENABLED Builds) and is marked as pending future cleanup, while staying
    visible in the registry with its storage and environment material intact.
    There is deliberately no revival path: ``enable`` refuses a DEPRECATED
    Build, and re-installing the exact same version is rejected by the
    immutable Build uniqueness constraint, so the only way to serve this
    plugin version again is to install its package under a new version and
    enable the fresh Build (see review doc G4(1), B4a).
    """
    build = _find_build(session, build_key)
    if build.status not in ("READY", "ENABLED"):
        raise HubError(
            code="PLUGIN_BUILD_NOT_DEPRECATABLE",
            message="插件 Build 当前状态不允许废弃, 仅 READY 或 ENABLED 可废弃",
            status_code=409,
        )
    build.status = "DEPRECATED"
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="plugin_build.deprecate",
        resource_type="plugin_build",
        resource_id=build.build_key,
        ip=actor_ip(request),
    )
    session.commit()
    session.refresh(build)
    return _build_response(build)


@router.post(
    "/plugin-builds/{build_key}/enable",
    response_model=PluginBuildResponse,
    dependencies=[Depends(require_role("publisher"))],
)
def enable_build(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
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
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="plugin_build.enable",
        resource_type="plugin_build",
        resource_id=build.build_key,
        ip=actor_ip(request),
    )
    session.commit()
    session.refresh(build)
    return _build_response(build)


@router.post(
    "/plugin-builds/{build_key}/disable",
    response_model=PluginBuildResponse,
    dependencies=[Depends(require_role("publisher"))],
)
def disable_build(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
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
        audit(
            session,
            actor_type=actor.kind,
            actor_id=actor.id,
            actor_name=actor.name,
            action="plugin_build.disable",
            resource_type="plugin_build",
            resource_id=build.build_key,
            ip=actor_ip(request),
        )
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


def _version_sort_key(version: str) -> tuple[int, ...]:
    """Parse a dotted version into comparable integer parts.

    String comparison would rank "9.0" above "10.0"; each dot-separated segment
    is parsed as an integer (non-numeric segments fall back to 0) so that
    "1.0.0" -> (1, 0, 0), "9.0" -> (9, 0), "10.0" -> (10, 0).
    """
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def _plugin_summary(plugin: Plugin) -> PluginSummary:
    """Map one Plugin without exposing internal persistence metadata."""
    return PluginSummary(
        id=plugin.plugin_key,
        name=plugin.name,
        description=plugin.description,
        category=plugin.category,
        latest_version=max(
            (version.version for version in plugin.versions),
            key=_version_sort_key,
            default=None,
        ),
    )


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
