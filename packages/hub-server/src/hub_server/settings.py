"""Strict configuration models for the Hub server."""

import os
import platform
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


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


class AuthSettings(StrictSettingsModel):
    """Public API authentication configuration."""

    mode: Literal["required", "off"] = "required"
    session_ttl_hours: int = Field(default=24, gt=0)
    login_failure_threshold: int = Field(default=5, gt=0)
    login_lockout_base_seconds: int = Field(default=30, gt=0)
    login_lockout_max_seconds: int = Field(default=900, gt=0)

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_yaml_boolean(cls, value: object) -> object:
        """YAML 1.1 parses bare `on`/`off` as booleans; accept both spellings."""
        if isinstance(value, bool):
            return "required" if value else "off"
        return value

    @model_validator(mode="after")
    def _lockout_ceiling_covers_the_first_lock(self) -> Self:
        """A ceiling below the base delay would shorten later locks, not cap them."""
        if self.login_lockout_max_seconds < self.login_lockout_base_seconds:
            raise ValueError(
                "login_lockout_max_seconds must not be below login_lockout_base_seconds"
            )
        return self


class WebhooksSettings(StrictSettingsModel):
    """Outbound webhook delivery policy and its SSRF guard.

    Deny-by-default: a callback must target a public host unless the deployment
    explicitly opts into private networks. The Hub API and the service manager both
    listen on loopback, so an unconstrained callback URL is a direct path to
    internal-only endpoints. ``allowed_hosts`` narrows the set of permitted hosts
    on top of the address guard; it never widens it.
    """

    allow_private_networks: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)

    @field_validator("allowed_hosts")
    @classmethod
    def _normalize_allowed_hosts(cls, value: list[str]) -> list[str]:
        """Compare hosts case-insensitively and without a trailing root dot."""
        normalized: list[str] = []
        for entry in value:
            host = entry.strip().lower().rstrip(".")
            if not host or host.startswith(".") or "/" in host:
                raise ValueError(f"allowed_hosts entry is not a host name: {entry!r}")
            normalized.append(host)
        return normalized


class QuotasSettings(StrictSettingsModel):
    """Global storage and concurrency quota defaults (per-user override in DB)."""

    enabled: bool = True
    max_total_bytes: int = Field(default=1024**4, gt=0)
    max_file_count: int = Field(default=10000, gt=0)
    max_concurrent_jobs: int = Field(default=8, gt=0)


class RetentionSettings(StrictSettingsModel):
    """File lifecycle and backup retention configuration."""

    input_ttl_hours: int = Field(default=720, gt=0)
    sweep_interval_minutes: int = Field(default=30, gt=0)
    backup_keep: int = Field(default=3, gt=0)
    job_retention_days: int = Field(default=30, gt=0)
    audit_retention_days: int = Field(default=180, gt=0)


class PluginsSettings(StrictSettingsModel):
    """Kernel feature plugin switches (V4.0)."""

    enabled: list[str] | None = None
    options: dict[str, dict[str, object]] = Field(default_factory=dict)


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
    auth: AuthSettings = Field(default_factory=AuthSettings)
    plugins: PluginsSettings = Field(default_factory=PluginsSettings)
    quotas: QuotasSettings = Field(default_factory=QuotasSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    webhooks: WebhooksSettings = Field(default_factory=WebhooksSettings)
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
        if mode := os.environ.get("HUB_AUTH_MODE"):
            value["auth"] = {**value.get("auth", {}), "mode": mode}
        return cls.model_validate(value)
