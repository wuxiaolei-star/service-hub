"""SDK-local strict JSON validation with no contracts-package dependency."""

import math
from typing import Any

MAX_JSON_DEPTH = 8
MAX_JSON_CONTAINER_ITEMS = 1024


def validate_json_value(
    value: Any,
    *,
    max_depth: int = MAX_JSON_DEPTH,
    max_container_items: int = MAX_JSON_CONTAINER_ITEMS,
    _depth: int = 0,
) -> Any:
    """Return an unchanged value only when it is losslessly JSON-safe and bounded."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value

    if type(value) is list:
        if _depth >= max_depth:
            raise ValueError("JSON value exceeds the maximum nesting depth")
        if len(value) > max_container_items:
            raise ValueError("JSON list exceeds the maximum item count")
        for item in value:
            validate_json_value(
                item,
                max_depth=max_depth,
                max_container_items=max_container_items,
                _depth=_depth + 1,
            )
        return value

    if type(value) is dict:
        if _depth >= max_depth:
            raise ValueError("JSON value exceeds the maximum nesting depth")
        if len(value) > max_container_items:
            raise ValueError("JSON object exceeds the maximum item count")
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("JSON object keys must be strings")
            validate_json_value(
                item,
                max_depth=max_depth,
                max_container_items=max_container_items,
                _depth=_depth + 1,
            )
        return value

    raise ValueError(f"unsupported JSON value type: {type(value).__name__}")
