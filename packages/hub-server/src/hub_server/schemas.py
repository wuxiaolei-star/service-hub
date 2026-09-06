"""Response schemas exposed by Hub system endpoints."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["UP"]


class PlatformResponse(BaseModel):
    os: Literal["linux"]
    arch: Literal["amd64", "arm64"]


class SystemInfoResponse(BaseModel):
    hub_version: str
    platform: PlatformResponse
    python_version: str
    deployment_mode: Literal["offline"]
