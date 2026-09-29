"""Typed stdout events emitted by the Hub runner."""

import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError

from .common import StrictContractModel

RUNNER_EVENT_PREFIX = "@@HUB@@"

# Audit M-4: a runner event message is bounded so one chatty plugin line can
# neither bloat the server-side events.jsonl nor the SSE stream. Runners
# truncate over-long messages before forwarding (see :func:`truncate_runner_line`).
RUNNER_MESSAGE_MAX_LENGTH = 4096
RUNNER_MESSAGE_TRUNCATION_SUFFIX = "…[truncated]"


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
    percent: int = Field(strict=True, ge=0, le=100)
    message: Annotated[str, Field(min_length=1, max_length=RUNNER_MESSAGE_MAX_LENGTH)] | None = None


class LogEvent(StrictContractModel):
    """A structured runner log message."""

    protocol_version: Literal["1.0"]
    type: Literal["log"]
    level: LogLevel
    message: Annotated[str, Field(min_length=1, max_length=RUNNER_MESSAGE_MAX_LENGTH)]


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


def truncate_runner_line(line: str) -> str:
    """Clamp an over-long event message so the line validates again (audit M-4).

    A plugin that emits a message beyond :data:`RUNNER_MESSAGE_MAX_LENGTH`
    would otherwise fail contract validation and take the whole Job down with
    a ``RunnerEventParseError``. The runner rewrites only the ``message``
    member, marks the cut with ``…[truncated]``, and leaves every other line —
    ordinary stdout, unparsable payloads, within-limit messages — untouched.
    """
    if not line.startswith(RUNNER_EVENT_PREFIX):
        return line
    try:
        document = json.loads(line.removeprefix(RUNNER_EVENT_PREFIX))
    except json.JSONDecodeError:
        return line
    if not isinstance(document, dict):
        return line
    message = document.get("message")
    if not isinstance(message, str) or len(message) <= RUNNER_MESSAGE_MAX_LENGTH:
        return line
    keep = RUNNER_MESSAGE_MAX_LENGTH - len(RUNNER_MESSAGE_TRUNCATION_SUFFIX)
    document["message"] = message[: max(keep, 1)] + RUNNER_MESSAGE_TRUNCATION_SUFFIX
    return RUNNER_EVENT_PREFIX + json.dumps(document, ensure_ascii=False)
