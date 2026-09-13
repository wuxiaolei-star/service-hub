"""Public plugin registry browse and Build package download endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from starlette.responses import FileResponse as StreamingFileResponse

from hub_server.dependencies import get_session, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import Plugin, PluginBuild, PluginVersion
from hub_server.services.audit import record as audit
from hub_server.storage import LocalStorage

router = APIRouter(
    prefix="/registry", tags=["registry"], dependencies=[Depends(require_role("viewer"))]
)

# The stored package payload of a Build is the zstd-compressed runtime archive
# named by the same layout rule used when verifying and installing packages.
_RUNTIME_ARCHIVE_SUFFIXES = {"conda-pack": "runtime/env.tar.zst", "docker": "image.tar.zst"}


class RegistryBuildItem(BaseModel):
    """One downloadable Build row of the flat registry listing."""

    plugin_id: str
    plugin_name: str
    version: str
    build_key: str
    runtime_type: str
    target_arch: str
    status: str
    package_sha256: str


class RegistryListResponse(BaseModel):
    items: list[RegistryBuildItem]


@router.get("/plugins", response_model=RegistryListResponse)
def list_registry_plugins(
    session: Annotated[Session, Depends(get_session)],
) -> RegistryListResponse:
    """Return every Build as one flat row ordered by plugin, version, and runtime."""
    builds = session.scalars(
        select(PluginBuild)
        .join(PluginBuild.plugin_version)
        .join(PluginVersion.plugin)
        .options(
            selectinload(PluginBuild.plugin_version).selectinload(PluginVersion.plugin)
        )
        .order_by(
            Plugin.plugin_key,
            PluginVersion.version,
            PluginBuild.runtime_type,
            PluginBuild.build_key,
        )
    ).all()
    return RegistryListResponse(items=[_registry_item(build) for build in builds])


@router.get(
    "/download/{build_key}",
    dependencies=[Depends(require_role("publisher"))],
)
def download_registry_package(
    actor: Annotated[Actor, Depends(get_actor)],
    request: Request,
    build_key: str,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> StreamingFileResponse:
    """Stream one Build package as an attachment and audit the download."""
    build = _find_build(session, build_key)
    package_path = _package_file(storage, build)
    version = build.plugin_version
    plugin_id = version.plugin.plugin_key
    attachment_name = f"{plugin_id}-{version.version}-{build.runtime_type}.pypkg"
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="registry.download",
        resource_type="plugin_build",
        resource_id=build.build_key,
        ip=actor_ip(request),
    )
    session.commit()
    return StreamingFileResponse(
        path=package_path,
        media_type="application/zstd",
        filename=attachment_name,
    )


def _registry_item(build: PluginBuild) -> RegistryBuildItem:
    version = build.plugin_version
    return RegistryBuildItem(
        plugin_id=version.plugin.plugin_key,
        plugin_name=version.plugin.name,
        version=version.version,
        build_key=build.build_key,
        runtime_type=build.runtime_type,
        target_arch=build.target_arch,
        status=build.status,
        package_sha256=build.package_sha256,
    )


def _find_build(session: Session, build_key: str) -> PluginBuild:
    build = session.scalar(
        select(PluginBuild)
        .where(PluginBuild.build_key == build_key)
        .options(selectinload(PluginBuild.plugin_version).selectinload(PluginVersion.plugin))
    )
    if build is None:
        raise _build_not_found()
    return build


def _package_file(storage: LocalStorage, build: PluginBuild) -> Path:
    """Locate the stored zstd package payload below the Build directory."""
    if build.package_path is None:
        raise _build_not_found()
    candidates = [build.runtime_archive_path]
    suffix = _RUNTIME_ARCHIVE_SUFFIXES.get(build.runtime_type)
    if suffix is not None:
        candidates.append(f"{build.package_path}/{suffix}")
    for candidate in candidates:
        try:
            path = storage.open_relative(candidate)
        except (HubError, ValueError):
            continue
        try:
            is_file = path.is_file()
        except OSError:
            continue
        if is_file:
            return path
    raise _build_not_found()


def _build_not_found() -> HubError:
    return HubError(
        code="PLUGIN_BUILD_NOT_FOUND",
        message="插件 Build 不存在",
        status_code=404,
    )
