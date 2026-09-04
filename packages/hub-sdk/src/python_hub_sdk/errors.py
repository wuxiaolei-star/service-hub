"""Stable, JSON-safe errors exposed by the plugin SDK."""

import json
import re
from dataclasses import dataclass
from typing import Any

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
                json.dumps(self.details, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("error details must be JSON serializable") from exc
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
