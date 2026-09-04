"""Serialized job runtime and result contracts."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .build_manifest import Sha256
from .common import PluginId, RelativeProtocolPath, SemanticVersion, StrictContractModel


class JobStatus(StrEnum):
    """Lifecycle states used by the Hub job state machine."""

    PENDING = "PENDING"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FileRole(StrEnum):
    """The direction of a file in a job runtime protocol."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class RuntimeJob(StrictContractModel):
    """The immutable identity and creation time of a job."""

    id: str = Field(min_length=1)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Require explicit offsets in serialized protocol timestamps."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class RuntimePlugin(StrictContractModel):
    """Resolved plugin build selected for a job."""

    id: PluginId
    version: SemanticVersion
    build_id: str = Field(min_length=1)


class RuntimeInputFile(StrictContractModel):
    """One immutable input snapshot exposed inside a job directory."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    path: RelativeProtocolPath
    size: int = Field(gt=0)
    extension: str = Field(min_length=1)
    sha256: Sha256


RuntimeInputFiles = Annotated[list[RuntimeInputFile], Field(min_length=1)]
RuntimeInputValue = RuntimeInputFile | RuntimeInputFiles


class RuntimeDirectories(StrictContractModel):
    """Named safe-relative directories below the job root."""

    input: RelativeProtocolPath
    work: RelativeProtocolPath
    output: RelativeProtocolPath
    logs: RelativeProtocolPath


class RuntimeExecution(StrictContractModel):
    """Hub-enforced execution configuration frozen with the job."""

    timeout: int = Field(gt=0)


class JobRuntimeSpec(StrictContractModel):
    """The read-only ``job.json`` contract consumed by a runner."""

    protocol_version: Literal["1.0"]
    job: RuntimeJob
    plugin: RuntimePlugin
    params: dict[str, Any]
    inputs: dict[str, RuntimeInputValue]
    directories: RuntimeDirectories
    execution: RuntimeExecution


class RuntimeOutputFile(StrictContractModel):
    """One final file registered by the runner in ``result.json``."""

    name: str = Field(min_length=1)
    path: RelativeProtocolPath
    format: str = Field(min_length=1)
    size: int = Field(gt=0)
    sha256: Sha256


class JobError(StrictContractModel):
    """Stable error information safe to persist and expose to callers."""

    type: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class JobResult(StrictContractModel):
    """The atomically written ``result.json`` contract."""

    protocol_version: Literal["1.0"]
    job_id: str = Field(min_length=1)
    status: JobStatus
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)
    message: str = Field(min_length=1)
    data: dict[str, Any]
    files: list[RuntimeOutputFile] = Field(default_factory=list)
    error: JobError | None

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject naive result timestamps before they reach persistence."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_terminal_status(self) -> Self:
        """Keep terminal result status and stable error information consistent."""
        if self.status is JobStatus.SUCCESS and self.error is not None:
            raise ValueError("successful results must not include an error")
        if self.status is JobStatus.FAILED and self.error is None:
            raise ValueError("failed results must include an error")
        return self
