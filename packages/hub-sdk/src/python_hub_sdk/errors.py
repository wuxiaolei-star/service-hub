"""Stable, JSON-safe errors exposed by the plugin SDK."""

import re
from dataclasses import dataclass
from typing import Any

from ._json_values import validate_json_value

_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


@dataclass(frozen=True, slots=True)
class PluginError(Exception):
    code: str
    message: str
    details: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not _CODE_PATTERN.fullmatch(self.code):
            raise ValueError("error code must match ^[A-Z][A-Z0-9_]{2,63}$")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("error message must be a non-empty string")
        if self.details is not None:
            try:
                validate_json_value(self.details)
            except ValueError as exc:
                raise ValueError("error details must be strict JSON") from exc
        Exception.__init__(self, self.message)

    @property
    def error_type(self) -> str:
        return type(self).__name__


@dataclass(frozen=True, slots=True)
class PluginValidationError(PluginError):
    pass


@dataclass(frozen=True, slots=True)
class PluginExecutionError(PluginError):
    pass


@dataclass(frozen=True, slots=True)
class PluginCancelledError(PluginError):
    code: str = "PLUGIN_CANCELLED"
    message: str = "任务已取消"
    details: Any = None
