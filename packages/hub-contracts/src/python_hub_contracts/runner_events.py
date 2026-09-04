"""Typed stdout events emitted by the Hub runner."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError

from .common import StrictContractModel

RUNNER_EVENT_PREFIX = "@@HUB@@"


class LogLevel(StrEnum):
    """Log levels accepted by the V1 runner event protocol."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ProgressEvent(StrictContractModel):
    """A runner-reported completion percentage and message."""

    protocol_version: Literal["1.0"]
    type: Literal["progress"]
    percent: int = Field(ge=0, le=100)
    message: str = Field(min_length=1)


class LogEvent(StrictContractModel):
    """A structured runner log message."""

    protocol_version: Literal["1.0"]
    type: Literal["log"]
    level: LogLevel
    message: str = Field(min_length=1)


RunnerEvent = Annotated[ProgressEvent | LogEvent, Field(discriminator="type")]
_runner_event_adapter: TypeAdapter[RunnerEvent] = TypeAdapter(RunnerEvent)


class RunnerEventParseError(ValueError):
    """A stable error for invalid, prefixed runner event lines."""

    def __init__(self, *, code: str = "RUNNER_EVENT_INVALID", message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def parse_runner_line(line: str) -> RunnerEvent | None:
    """Parse one prefixed runner event, leaving ordinary stdout untouched."""
    if not line.startswith(RUNNER_EVENT_PREFIX):
        return None

    payload = line.removeprefix(RUNNER_EVENT_PREFIX)
    try:
        return _runner_event_adapter.validate_json(payload)
    except ValidationError as error:
        raise RunnerEventParseError(message="invalid runner event") from error
