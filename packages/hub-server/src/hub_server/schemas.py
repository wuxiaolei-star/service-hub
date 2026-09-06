"""Request and response schemas exposed by Hub HTTP endpoints."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from python_hub_contracts import (
    JobResult,
    JobRuntimeSpec,
    RelativeProtocolPath,
    RunnerEvent,
    RuntimeBuild,
    RuntimeType,
)


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


class InternalSchema(BaseModel):
    """Strict payload base for the private runner protocol."""

    model_config = ConfigDict(extra="forbid")


class RunnerClaimRequest(InternalSchema):
    runtime_type: RuntimeType


class RunnerBuildClaim(InternalSchema):
    build_id: str
    runtime_type: RuntimeType
    runtime: RuntimeBuild
    runtime_archive: RelativeProtocolPath
    manifest: RelativeProtocolPath
    source: RelativeProtocolPath
    environment_path: RelativeProtocolPath | None = None
    image_digest: str | None = None


class RunnerOperationClaimResponse(InternalSchema):
    operation_id: str
    runtime_type: RuntimeType
    kind: Literal["INSTALL"]
    timeout_seconds: int | None
    build: RunnerBuildClaim


class RunnerOperationCompleteRequest(InternalSchema):
    runtime_type: RuntimeType
    status: Literal["SUCCESS", "FAILED"]
    environment_path: RelativeProtocolPath | None = None
    image_digest: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    error_summary: Annotated[str, Field(max_length=2000)] | None = None
    exit_code: int | None = None


class RunnerCompletionResponse(InternalSchema):
    id: str
    status: Literal["SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT"]


class RunnerJobPaths(InternalSchema):
    job: Literal["job.json"] = "job.json"
    result: Literal["result.json"] = "result.json"


class RunnerJobClaimResponse(InternalSchema):
    job_id: str
    runtime_type: RuntimeType
    cancel_requested: bool
    workspace: RelativeProtocolPath
    paths: RunnerJobPaths
    job: JobRuntimeSpec
    build: RunnerBuildClaim


class RunnerJobEventRequest(InternalSchema):
    runtime_type: RuntimeType
    event: RunnerEvent


class RunnerJobCompleteRequest(InternalSchema):
    runtime_type: RuntimeType
    result: JobResult
    exit_code: int | None = None


class RunnerEventAcceptedResponse(InternalSchema):
    id: str
    status: Literal["RUNNING"]
