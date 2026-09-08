"""Staged plugin Build registration and storage promotion."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import PluginBuild, RunnerOperation
from hub_server.repositories import HubRepository
from hub_server.services.archives import VerifiedPluginPackage
from hub_server.storage import LocalStorage


class PluginService:
    """Coordinate verified package storage with immutable Build metadata."""

    def __init__(
        self,
        session: Session,
        storage: LocalStorage,
        *,
        platform_os: Literal["linux"],
        platform_arch: Literal["amd64", "arm64"],
    ) -> None:
        self._session = session
        self._storage = storage
        self._platform_os = platform_os
        self._platform_arch = platform_arch

    def stage_installation(
        self, verified: VerifiedPluginPackage, *, package_sha256: str
    ) -> PluginBuild:
        """Persist identity, then atomically promote verified contents under the Build key."""
        staging = verified.source_dir.parent
        self._validate_verified_paths(verified, staging)
        try:
            self._validate_platform(verified)
            build = HubRepository(self._session).create_build_installation(
                plugin_manifest=verified.manifest,
                build_manifest=verified.build,
                package_sha256=package_sha256,
            )
        except IntegrityError as error:
            self._storage.discard_staged_plugin(staging)
            raise HubError(
                code="PLUGIN_BUILD_ALREADY_EXISTS",
                message="相同版本、平台和运行时的插件 Build 已存在",
                status_code=409,
            ) from error
        except Exception:
            self._storage.discard_staged_plugin(staging)
            raise

        destination = None
        try:
            destination = self._storage.install_staged_plugin(staging, build.build_key)
            build_root = self._storage.relative_path(destination)
            archive_suffix = verified.build.runtime.archive
            build.package_path = build_root
            build.runtime_archive_path = f"{build_root}/{archive_suffix}"
            operation = self._session.scalar(
                select(RunnerOperation).where(RunnerOperation.plugin_build_id == build.id)
            )
            if operation is None:
                raise RuntimeError("installation operation was not persisted")
            operation.payload_json = {
                **operation.payload_json,
                "runtime_type": build.runtime_type,
                "runtime_archive": build.runtime_archive_path,
                "manifest_path": f"{build_root}/plugin.yaml",
                "source_path": f"{build_root}/plugin",
            }
            self._session.commit()
        except Exception:
            self._session.rollback()
            if destination is not None:
                self._storage.remove_plugin_build(build.build_key)
            self._storage.discard_staged_plugin(staging)
            self._mark_failed(build)
            raise
        self._session.refresh(build)
        return build

    def _validate_platform(self, verified: VerifiedPluginPackage) -> None:
        target = verified.build.target
        if target.os != self._platform_os or target.arch != self._platform_arch:
            raise HubError(
                code="PLUGIN_BUILD_PLATFORM_MISMATCH",
                message="插件 Build 与当前 Hub 平台不匹配",
                status_code=422,
            )

    @staticmethod
    def _validate_verified_paths(
        verified: VerifiedPluginPackage, staging: Path
    ) -> None:
        root = staging.resolve()
        try:
            verified.archive.resolve().relative_to(root)
        except ValueError as error:
            raise ValueError("verified package paths must share one staging root") from error
        if verified.source_dir.resolve() != root / "plugin":
            raise ValueError("verified plugin source directory is invalid")

    def _mark_failed(self, build: PluginBuild) -> None:
        """Best-effort persistence of a failed promotion without masking its cause."""
        try:
            persisted = self._session.get(PluginBuild, build.id)
            if persisted is None:
                return
            persisted.status = "FAILED"
            persisted.error_summary = "插件 Build 存储安装失败"
            if persisted.environment is not None:
                persisted.environment.status = "FAILED"
                persisted.environment.error_summary = persisted.error_summary
            for operation in persisted.operations:
                if operation.status == "PENDING":
                    operation.status = "FAILED"
                    operation.error_summary = persisted.error_summary
            self._session.commit()
        except Exception:
            self._session.rollback()
