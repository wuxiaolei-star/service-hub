"""Platform-specific plugin build manifest contracts."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, StringConstraints, field_validator

from .common import (
    PluginId,
    RelativeProtocolPath,
    SemanticVersion,
    StrictContractModel,
    normalize_arch,
    normalize_os,
)
from .plugin_manifest import PluginManifest, PythonVersion

Sha256 = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


def _normalize_target_os(value: object) -> object:
    if isinstance(value, str):
        return normalize_os(value)
    return value


def _normalize_target_arch(value: object) -> object:
    if isinstance(value, str):
        return normalize_arch(value)
    return value


class TargetPlatform(StrictContractModel):
    """Normalized V1 deployment platform."""

    os: Annotated[Literal["linux"], BeforeValidator(_normalize_target_os)]
    arch: Annotated[Literal["amd64", "arm64"], BeforeValidator(_normalize_target_arch)]


RuntimeType = Literal["conda-pack", "docker"]


class CondaPackRuntime(StrictContractModel):
    """Immutable conda-pack environment metadata."""

    type: Literal["conda-pack"]
    archive: RelativeProtocolPath
    fingerprint: Sha256


class DockerRuntime(StrictContractModel):
    """Immutable Docker image metadata."""

    type: Literal["docker"]
    archive: RelativeProtocolPath
    image: str = Field(min_length=1, max_length=255)
    digest: Sha256


RuntimeBuild = Annotated[CondaPackRuntime | DockerRuntime, Field(discriminator="type")]


class PluginBuildManifest(StrictContractModel):
    """Platform-specific, immutable plugin build metadata."""

    schema_version: Literal["1.0"]
    build_id: str = Field(min_length=1)
    plugin_id: PluginId
    plugin_version: SemanticVersion
    target: TargetPlatform
    python_version: PythonVersion
    runtime: RuntimeBuild
    sdk_version: SemanticVersion
    source_sha256: Sha256
    built_at: datetime

    @field_validator("built_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require audit timestamps to identify an unambiguous instant."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    def assert_matches_plugin(self, plugin: PluginManifest) -> None:
        """Raise when build metadata is incompatible with its source manifest."""
        mismatches: list[str] = []
        if self.plugin_id != plugin.plugin.id:
            mismatches.append("plugin ID")
        if self.plugin_version != plugin.plugin.version:
            mismatches.append("plugin version")
        if self.python_version != plugin.runtime.python.version:
            mismatches.append("Python version")
        if self.sdk_version.partition(".")[0] != plugin.sdk.version.partition(".")[0]:
            mismatches.append("SDK major version")
        if mismatches:
            raise ValueError(f"build does not match plugin: {', '.join(mismatches)}")
