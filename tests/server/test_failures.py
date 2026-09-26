"""G8 failure classification rules for terminal Job outcomes (B8)."""

from __future__ import annotations

import pytest
from hub_server.services.failures import (
    FAILURE_CLASSES,
    REAPER_SOURCE,
    RECONCILE_SOURCE,
    RUNNER_SOURCE,
    classify_failure,
    failure_summary,
)
from python_hub_contracts import JobError, JobStatus

_REAPER_SUMMARY = "Runner 失联，任务被 reaper 回收"  # noqa: RUF001


def test_failed_arbitrary_plugin_code_classifies_plugin() -> None:
    """The plugin SDK lets plugins pick any code; a runner completion stores it."""
    assert (
        classify_failure("FAILED", "SHAPEFILE_WRITE_FAILED: 网格写出失败", RUNNER_SOURCE)
        == "plugin"
    )


def test_failed_plugin_namespaced_code_classifies_plugin() -> None:
    assert classify_failure("FAILED", "PLUGIN_VALIDATION_FAILED: 参数越界") == "plugin"


def test_failed_output_missing_classifies_plugin() -> None:
    """G6/R5 enforcement failures attribute to the plugin that skipped an output."""
    assert classify_failure("FAILED", "PLUGIN_OUTPUT_MISSING: result_files") == "plugin"


def test_failed_runner_fallback_code_classifies_runner() -> None:
    assert classify_failure("FAILED", "RUNNER_FAILED: Runner failed") == "runner"


def test_failed_hub_restarted_classifies_runner() -> None:
    assert classify_failure("FAILED", "HUB_RESTARTED") == "runner"


def test_failed_timeout_code_classifies_timeout() -> None:
    assert classify_failure("FAILED", "JOB_TIMED_OUT: Job timed out") == "timeout"


def test_timed_out_status_classifies_timeout_regardless_of_summary() -> None:
    assert classify_failure(JobStatus.TIMED_OUT, "JOB_TIMED_OUT") == "timeout"
    assert classify_failure(JobStatus.TIMED_OUT, None) == "timeout"
    assert classify_failure("TIMED_OUT", _REAPER_SUMMARY, REAPER_SOURCE) == "timeout"


def test_cancelled_status_wins_over_misleading_summary() -> None:
    assert (
        classify_failure("CANCELLED", "RUNNER_FAILED: Runner failed") == "cancelled"
    )
    assert classify_failure(JobStatus.CANCELLED, "PLUGIN_CANCELLED: 任务已取消") == (
        "cancelled"
    )


@pytest.mark.parametrize(
    "summary",
    [None, "", "Docker container exited with status 1", "conda runner failed"],
)
def test_failed_prose_or_missing_summary_is_system(summary: str | None) -> None:
    assert classify_failure("FAILED", summary) == "system"


def test_failed_unknown_code_from_non_runner_source_is_system() -> None:
    assert (
        classify_failure("FAILED", "MYSTERY_CODE: boom", RECONCILE_SOURCE) == "system"
    )
    assert classify_failure("FAILED", "MYSTERY_CODE: boom", REAPER_SOURCE) == "system"


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("ABC: three-letter minimum", "plugin"),
        ("AB: too short to be a code", "system"),
        ("abc: lowercase never matches", "system"),
        ("WARNING issued: prose with a colon", "system"),
    ],
)
def test_failed_code_prefix_shape_boundaries(summary: str, expected: str) -> None:
    assert classify_failure("FAILED", summary) == expected


@pytest.mark.parametrize("status", ["SUCCESS", "PENDING", "PREPARING", "RUNNING"])
def test_success_and_non_terminal_statuses_have_no_class(status: str) -> None:
    with pytest.raises(ValueError, match="terminal failure"):
        classify_failure(status, "RUNNER_FAILED: Runner failed")


def test_jobstatus_enum_and_stored_string_agree() -> None:
    for status_value in ("FAILED", "TIMED_OUT", "CANCELLED"):
        assert classify_failure(JobStatus(status_value), None) == classify_failure(
            status_value, None
        )


def test_failure_classes_covers_exactly_the_five_classes() -> None:
    assert set(FAILURE_CLASSES) == {"plugin", "timeout", "cancelled", "runner", "system"}


def test_failure_summary_normalizes_runner_error_to_code_prefix() -> None:
    error = JobError(
        type="PluginExecutionError", code="NC_READ_FAILED", message="读取失败"
    )
    assert failure_summary(error) == "NC_READ_FAILED: 读取失败"


def test_failure_summary_passes_missing_error_through() -> None:
    assert failure_summary(None) is None
