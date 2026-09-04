"""Value objects returned by Python Service Hub plugins."""

import json
from dataclasses import dataclass, field
from pathlib import PurePath, PureWindowsPath


@dataclass(frozen=True, slots=True)
class InputFile:
    id: str
    name: str
    path: str
    size: int
    extension: str
    sha256: str

    def __post_init__(self) -> None:
        if not self.id or not self.name or not self.path:
            raise ValueError("InputFile id, name, and path must be non-empty")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ValueError("InputFile size must be a non-negative integer")
        if not self.extension:
            raise ValueError("InputFile extension must be non-empty")
        if len(self.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in self.sha256):
            raise ValueError("InputFile sha256 must be a 64-character hexadecimal digest")


def _validate_relative_path(path: str) -> None:
    if not isinstance(path, str) or not path:
        raise ValueError("OutputFile path must be a non-empty relative path")
    if (
        path.startswith(("/", "\\"))
        or PurePath(path).is_absolute()
        or PureWindowsPath(path).is_absolute()
    ):
        raise ValueError("OutputFile path must be relative")
    if ".." in PurePath(path).parts or ".." in PureWindowsPath(path).parts:
        raise ValueError("OutputFile path must not traverse parent directories")


@dataclass(frozen=True, slots=True)
class OutputFile:
    name: str
    path: str
    format: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("OutputFile name must be non-empty")
        _validate_relative_path(self.path)
        if self.format == "":
            raise ValueError("OutputFile format must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class PluginResult:
    message: str | None = None
    data: dict[str, object] = field(default_factory=dict)
    files: list[OutputFile] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.message is not None and not isinstance(self.message, str):
            raise ValueError("PluginResult message must be a string or None")
        if not isinstance(self.data, dict) or not isinstance(self.files, list):
            raise ValueError("PluginResult data and files must be a dict and list")
        try:
            json.dumps(self.data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("PluginResult data must be JSON serializable") from exc
