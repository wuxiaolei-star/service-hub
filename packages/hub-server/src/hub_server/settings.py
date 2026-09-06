"""Strict configuration models for the Hub server."""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field


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


class HubSettings(StrictSettingsModel):
    deployment: DeploymentSettings
    storage: StorageSettings
    database: DatabaseSettings
    uploads: UploadSettings
    hub_version: str = "0.1.0"
    platform_os: Literal["linux"] = "linux"
    platform_arch: Literal["amd64", "arm64"] = "amd64"

    @classmethod
    def from_yaml(cls, path: Path) -> Self:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Hub 配置根节点必须是对象")
        return cls.model_validate(value)
