"""Request and response schemas exposed by Hub HTTP endpoints."""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from python_hub_contracts import (
    JobResult,
    JobRuntimeSpec,
    JobStatus,
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
    created_at: datetime


class PluginBuildResponse(BaseModel):
    build_id: str
    plugin_id: str
    version: str
    runtime_type: RuntimeType
    target_os: str
    target_arch: str
    status: str
    package_sha256: str
    runtime_fingerprint: str
    error_summary: str | None = None


class PluginSummary(BaseModel):
    id: str
    name: str
    description: str | None = None
    category: str | None = None
    latest_version: str | None = None


class PluginVersionDetail(BaseModel):
    version: str
    spec_version: str
    sdk_version: str
    source_sha256: str
    status: str
    manifest: dict[str, object]


class PluginDetailResponse(PluginSummary):
    author: str | None = None
    versions: list[PluginVersionDetail]


class PluginListResponse(BaseModel):
    items: list[PluginSummary]


class PluginBuildListResponse(BaseModel):
    items: list[PluginBuildResponse]


class JobCallbackRequest(BaseModel):
    """Optional terminal-state webhook registration for one Job."""

    url: str = Field(min_length=1, max_length=1024)
    secret: str | None = Field(default=None, max_length=255)


class JobCreateRequest(BaseModel):
    plugin_id: str
    version: str
    runtime_type: RuntimeType | None = None
    inputs: dict[str, object]
    params: dict[str, object] = Field(default_factory=dict)
    callback: JobCallbackRequest | None = None


class JobResponse(BaseModel):
    job_id: str
    plugin_id: str
    version: str
    build_id: str
    runtime_type: RuntimeType
    status: JobStatus
    cancel_requested: bool = False
    error_summary: str | None = None
    replayed_from: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int = 0


class JobStatsBucket(BaseModel):
    """Per-day Job volume and duration percentiles for one UTC calendar day."""

    date: str
    count: int
    success_count: int
    p50_ms: int | None = None
    p95_ms: int | None = None


class JobStatsResponse(BaseModel):
    days: int
    buckets: list[JobStatsBucket]


class FileListResponse(BaseModel):
    items: list[FileResponse]


# Service host ports must stay unprivileged and never collide with the Hub's
# own loopback listeners (hub-api 8000, service-manager 8001, web console 8080).
_SERVICE_HOST_PORT_MIN = 1024
_RESERVED_SERVICE_HOST_PORTS = frozenset({8000, 8001, 8080})
# Service containers always run as an explicit non-root "uid:gid" pair.
_SERVICE_USER_LABEL_PATTERN = re.compile(r"^[1-9]\d{0,9}:[1-9]\d{0,9}$")


class ServicePort(BaseModel):
    """One host-loopback port mapping approved for a service container."""

    host: int = Field(ge=_SERVICE_HOST_PORT_MIN, le=65535)
    container: int = Field(ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def host_must_not_be_reserved(cls, value: int) -> int:
        """Keep managed services away from the Hub's own published ports."""
        if value in _RESERVED_SERVICE_HOST_PORTS:
            raise ValueError("host port is reserved for the Service Hub itself")
        return value


class ServiceMount(BaseModel):
    """One admin-supplied bind mount forwarded only to service-manager."""

    source: str = Field(min_length=1, max_length=1024)
    target: str = Field(min_length=1, max_length=1024)
    read_only: bool = False

    @field_validator("source", "target")
    @classmethod
    def path_must_be_absolute_without_parent_segments(cls, value: str) -> str:
        """Reject relative paths, drive letters, and any `..` escape segment."""
        if not value.startswith("/") or ".." in value.split("/"):
            raise ValueError("mount paths must be absolute without parent segments")
        return value


class ServiceCreateRequest(BaseModel):
    """Configuration for a managed service; accepted from administrators only."""

    name: str = Field(min_length=1, max_length=63)
    image: str = Field(min_length=1, max_length=255)
    ports: list[ServicePort] = Field(default_factory=list, max_length=32)
    env: dict[str, str] = Field(default_factory=dict)
    mounts: list[ServiceMount] = Field(default_factory=list, max_length=32)
    command: list[str] | None = Field(default=None, max_length=64)
    user_label: str = Field(default="65532:65532", min_length=1, max_length=64)

    @field_validator("user_label")
    @classmethod
    def user_label_must_be_a_non_root_pair(cls, value: str) -> str:
        """Service containers may never request uid 0 or named users."""
        if _SERVICE_USER_LABEL_PATTERN.fullmatch(value) is None:
            raise ValueError("user_label must be a non-root numeric uid:gid pair")
        return value


class ServiceRuntimeResponse(BaseModel):
    """Live state sourced from service-manager rather than persisted input."""

    state: str | None = None
    health: str | None = None
    ports: dict[str, object] | None = None


class ServiceResponse(BaseModel):
    """Public service state, deliberately excluding environment and mount secrets."""

    name: str
    image: str
    container_name: str
    desired_state: str
    runtime: ServiceRuntimeResponse | None


class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]


class JobCancelResponse(BaseModel):
    job_id: str
    status: JobStatus
    cancel_requested: bool


class JobLogResponse(BaseModel):
    items: list[dict[str, object]]
    next_cursor: int | None = None


class JobOutputsResponse(BaseModel):
    items: list[FileResponse]


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


class RunnerReconcileResponse(InternalSchema):
    runtime_type: RuntimeType
    failed_jobs: int


class RunnerCancellationResponse(InternalSchema):
    job_id: str
    cancel_requested: bool


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
