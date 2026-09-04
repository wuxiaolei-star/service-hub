import pytest
from python_hub_sdk import (
    PluginCancelledError,
    PluginError,
    PluginExecutionError,
    PluginValidationError,
)


def test_plugin_validation_error_has_stable_fields() -> None:
    error = PluginValidationError(code="INVALID_TIME_RANGE", message="范围非法")
    assert str(error) == "范围非法"
    assert error.code == "INVALID_TIME_RANGE"
    assert error.error_type == "PluginValidationError"


def test_plugin_cancelled_error_has_defaults() -> None:
    error = PluginCancelledError()
    assert error.code == "PLUGIN_CANCELLED"
    assert error.message == "任务已取消"
    assert str(error) == "任务已取消"


@pytest.mark.parametrize("code", ["bad_code", "AB", "A" * 65, "1_BAD"])
def test_plugin_error_rejects_invalid_codes(code: str) -> None:
    with pytest.raises(ValueError):
        PluginError(code=code, message="message")


@pytest.mark.parametrize(
    "details",
    [{"x": object()}, {1: "x"}, {"value": float("nan")}, {"value": float("inf")}],
)
def test_plugin_error_details_must_be_strict_json(details: object) -> None:
    with pytest.raises(ValueError):
        PluginExecutionError(code="EXEC_FAILED", message="failed", details=details)


def test_plugin_error_preserves_nested_json_details() -> None:
    details = {"bounds": [1, 2.5, None, True]}

    error = PluginExecutionError(code="EXEC_FAILED", message="failed", details=details)

    assert error.details == details
