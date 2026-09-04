"""Shared strict primitives for the Python Service Hub protocol."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, StringConstraints


class StrictContractModel(BaseModel):
    """Base model that rejects unknown fields and cannot be mutated."""

    model_config = ConfigDict(extra="forbid", frozen=True)


PluginId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$"),
]
"""A stable, lowercase identifier for a plugin."""

SemanticVersion = Annotated[
    str,
    StringConstraints(pattern=r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$"),
]
"""A three-part semantic version without prerelease or build metadata."""


def _normalize_relative_protocol_path(value: object) -> object:
    """Normalize separators and reject unsafe serialized protocol paths."""
    if not isinstance(value, str):
        return value

    normalized = value.replace("\\", "/")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
    ):
        raise ValueError("path must be a safe relative protocol path")

    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValueError("path must not contain empty, current, or parent segments")

    return normalized


RelativeProtocolPath = Annotated[
    str,
    BeforeValidator(_normalize_relative_protocol_path),
    StringConstraints(min_length=1),
]
"""A normalized, safe relative path used in serialized protocol messages."""


def normalize_os(value: str) -> Literal["linux"]:
    """Normalize a supported operating-system name."""
    if value.casefold() in {"linux", "gnu/linux"}:
        return "linux"
    raise ValueError(f"unsupported operating system: {value}")


def normalize_arch(value: str) -> Literal["amd64", "arm64"]:
    """Normalize a supported architecture name."""
    aliases: dict[str, Literal["amd64", "arm64"]] = {
        "amd64": "amd64",
        "x86_64": "amd64",
        "x64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    try:
        return aliases[value.casefold()]
    except KeyError as error:
        raise ValueError(f"unsupported architecture: {value}") from error
