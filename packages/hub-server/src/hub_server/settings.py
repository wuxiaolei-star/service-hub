"""Strict configuration models for the Hub server."""

import os
import platform
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class StrictSettingsModel(BaseModel):
    """Base model that rejects configuration keys not understood by the Hub."""

    model_config = ConfigDict(extra="forbid")


class DeploymentSettings(StrictSettingsModel):
    mode: Literal["offline"]


class StorageSettings(StrictSettingsModel):
    root: Path


class DatabaseSettings(StrictSettingsModel):
    url: str


class UploadSettings(StrictSettingsModel):
    max_size_bytes: int = Field(gt=0)


class RunnerSettings(StrictSettingsModel):
    """Private coordination settings shared only with runtime runners."""

    shared_token: SecretStr
    poll_interval_seconds: int = Field(default=2, gt=0)


def _native_architecture() -> Literal["amd64", "arm64"]:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise ValueError(f"unsupported Hub architecture: {machine}")


class HubSettings(StrictSettingsModel):
    deployment: DeploymentSettings
    storage: StorageSettings
    database: DatabaseSettings
    uploads: UploadSettings
    runner: RunnerSettings
    hub_version: str = "0.1.0"
    platform_os: Literal["linux"] = "linux"
    platform_arch: Literal["amd64", "arm64"] = Field(default_factory=_native_architecture)

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Hub 配置根节点必须是对象")
        if token := os.environ.get("HUB_RUNNER_TOKEN"):
            value["runner"] = {**value.get("runner", {}), "shared_token": token}
        return cls.model_validate(value)
