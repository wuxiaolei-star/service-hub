"""Request and response schemas exposed by Hub HTTP endpoints."""

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


class FileResponse(BaseModel):
    """Public metadata for an available Hub file."""

    file_id: str
    name: str
    size: int
    sha256: str
    extension: str | None
    mime_type: str | None
    status: Literal["AVAILABLE"]


class ErrorBody(BaseModel):
    """A stable, client-safe API error description."""

    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    """The uniform envelope used for Hub API failures."""

    success: Literal[False] = False
    error: ErrorBody
