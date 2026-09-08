from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from python_hub_contracts import JobError, JobResult, JobRuntimeSpec, JobStatus

SHA256 = "a" * 64


@pytest.fixture
def valid_job() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "job": {"id": "job_01K123", "created_at": "2026-09-04T14:30:00+08:00"},
        "plugin": {"id": "nc_to_shp", "version": "1.0.0", "build_id": "build_01K123"},
        "params": {"start_time": 1, "end_time": 24, "target_epsg": 3857},
        "inputs": {
            "nc_file": {
                "id": "file_01K123",
                "name": "model.nc",
                "path": "input/model.nc",
                "size": 185624733,
                "extension": ".nc",
                "sha256": SHA256,
            }
        },
        "directories": {"input": "input", "work": "work", "output": "output", "logs": "logs"},
        "execution": {"timeout": 3600},
    }


@pytest.fixture
def successful_result() -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "job_id": "job_01K123",
        "status": "SUCCESS",
        "started_at": "2026-09-04T14:30:05+08:00",
        "finished_at": "2026-09-04T14:31:28+08:00",
        "duration_ms": 83000,
        "message": "NC转换完成",
        "data": {"time_count": 24, "point_count": 158624},
        "files": [
            {
                "name": "水深结果",
                "path": "depth.zip",
                "format": "zip",
                "size": 5823674,
                "sha256": SHA256,
            }
        ],
        "error": None,
    }


def test_design_job_runtime_spec_round_trips_losslessly(valid_job: dict[str, Any]) -> None:
    runtime = JobRuntimeSpec.model_validate(valid_job)

    round_tripped = JobRuntimeSpec.model_validate_json(runtime.model_dump_json())

    assert round_tripped == runtime
    assert runtime.protocol_version == "1.0"
    assert runtime.job.created_at.tzinfo is not None


@pytest.mark.parametrize("protocol_version", ["1.1", "2.0"])
def test_job_runtime_spec_rejects_non_v1_protocol_versions(
    protocol_version: str, valid_job: dict[str, Any]
) -> None:
    valid_job["protocol_version"] = protocol_version

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


def test_job_runtime_spec_rejects_naive_creation_timestamp(valid_job: dict[str, Any]) -> None:
    valid_job["job"]["created_at"] = "2026-09-04T14:30:00"

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


def test_input_file_set_must_not_be_empty(valid_job: dict[str, Any]) -> None:
    valid_job["inputs"]["nc_files"] = []

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


def test_input_file_set_accepts_non_empty_lists(valid_job: dict[str, Any]) -> None:
    second_file = deepcopy(valid_job["inputs"]["nc_file"])
    second_file["id"] = "file_01K456"
    second_file["name"] = "model-part-2.nc"
    second_file["path"] = "input/model-part-2.nc"
    valid_job["inputs"]["nc_files"] = [deepcopy(valid_job["inputs"]["nc_file"]), second_file]

    runtime = JobRuntimeSpec.model_validate(valid_job)

    assert [file.id for file in runtime.inputs["nc_files"]] == ["file_01K123", "file_01K456"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "../model.nc"),
        ("size", 0),
        ("sha256", "A" * 64),
    ],
)
def test_input_file_metadata_is_validated(
    field: str, value: str | int, valid_job: dict[str, Any]
) -> None:
    valid_job["inputs"]["nc_file"][field] = value

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


def test_runtime_timeout_must_be_positive(valid_job: dict[str, Any]) -> None:
    valid_job["execution"]["timeout"] = 0

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


@pytest.mark.parametrize(
    "params",
    [
        {1: "x"},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": (1, 2)},
    ],
)
def test_runtime_params_reject_non_json_values(
    params: dict[object, object], valid_job: dict[str, Any]
) -> None:
    valid_job["params"] = params

    with pytest.raises(ValidationError):
        JobRuntimeSpec.model_validate(valid_job)


def test_successful_result_round_trips_with_uppercase_enum_status(
    successful_result: dict[str, Any]
) -> None:
    result = JobResult.model_validate(successful_result)

    round_tripped = JobResult.model_validate_json(result.model_dump_json())

    assert round_tripped == result
    assert result.status is JobStatus.SUCCESS
    assert '"status":"SUCCESS"' in result.model_dump_json()
    assert result.started_at.tzinfo is not None
    assert result.finished_at.tzinfo is not None


def test_timed_out_result_requires_timeout_error(
    successful_result: dict[str, Any]
) -> None:
    successful_result["status"] = "TIMED_OUT"
    successful_result["error"] = {
        "type": "RunnerTimeout",
        "code": "JOB_TIMED_OUT",
        "message": "任务超过时限",
    }

    result = JobResult.model_validate(successful_result)

    assert result.status is JobStatus.TIMED_OUT


@pytest.mark.parametrize(
    "error",
    [
        None,
        {
            "type": "RunnerTimeout",
            "code": "EXEC_FAILED",
            "message": "任务超过时限",
        },
    ],
)
def test_timed_out_result_rejects_missing_or_wrong_error_code(
    error: dict[str, Any] | None, successful_result: dict[str, Any]
) -> None:
    successful_result["status"] = "TIMED_OUT"
    successful_result["error"] = error

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


@pytest.mark.parametrize("protocol_version", ["1.1", "2.0"])
def test_job_result_rejects_non_v1_protocol_versions(
    protocol_version: str, successful_result: dict[str, Any]
) -> None:
    successful_result["protocol_version"] = protocol_version

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


@pytest.mark.parametrize("timestamp", ["started_at", "finished_at"])
def test_job_result_rejects_naive_timestamps(
    timestamp: str, successful_result: dict[str, Any]
) -> None:
    successful_result[timestamp] = "2026-09-04T14:30:05"

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


@pytest.mark.parametrize("status", ["PENDING", "PREPARING", "RUNNING"])
def test_job_result_rejects_nonterminal_statuses(
    status: str, successful_result: dict[str, Any]
) -> None:
    successful_result["status"] = status

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", "../depth.zip"),
        ("format", ""),
        ("size", 0),
        ("sha256", "A" * 64),
    ],
)
def test_result_output_file_metadata_is_validated(
    field: str, value: str | int, successful_result: dict[str, Any]
) -> None:
    successful_result["files"][0][field] = value

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


def test_result_output_file_format_is_optional(successful_result: dict[str, Any]) -> None:
    successful_result["files"][0]["format"] = None

    result = JobResult.model_validate(successful_result)

    assert result.files[0].format is None


@pytest.mark.parametrize(
    "data",
    [
        {1: "x"},
        {"value": float("nan")},
        {"value": float("-inf")},
        {"value": (1, 2)},
    ],
)
def test_job_result_data_rejects_non_json_values(
    data: dict[object, object], successful_result: dict[str, Any]
) -> None:
    successful_result["data"] = data

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


def test_job_error_details_round_trip_without_coercion() -> None:
    error = JobError.model_validate(
        {
            "type": "PluginValidationError",
            "code": "INVALID_RANGE",
            "message": "invalid range",
            "details": {"bounds": [1, 2.5, None, True]},
        }
    )

    restored = JobError.model_validate_json(error.model_dump_json())

    assert restored == error
    assert restored.details == {"bounds": [1, 2.5, None, True]}


@pytest.mark.parametrize("details", [{1: "x"}, {"value": float("nan")}])
def test_job_error_details_reject_non_json_values(details: dict[object, object]) -> None:
    with pytest.raises(ValidationError):
        JobError.model_validate(
            {
                "type": "PluginExecutionError",
                "code": "EXEC_FAILED",
                "message": "failed",
                "details": details,
            }
        )


def test_success_requires_no_error(successful_result: dict[str, Any]) -> None:
    successful_result["error"] = {
        "type": "PluginValidationError",
        "code": "NC_VARIABLE_MISSING",
        "message": "missing stage",
    }

    with pytest.raises(ValidationError):
        JobResult.model_validate(successful_result)


def test_failed_result_requires_error_and_rejects_unregistered_output(
    successful_result: dict[str, Any]
) -> None:
    failed = deepcopy(successful_result)
    failed["status"] = "FAILED"
    failed["message"] = "任务执行失败"
    failed["data"] = {}
    failed["files"] = []
    failed["error"] = None

    with pytest.raises(ValidationError):
        JobResult.model_validate(failed)

    failed["error"] = {
        "type": "PluginValidationError",
        "code": "NC_VARIABLE_MISSING",
        "message": "NC文件中缺少 stage 变量",
    }
    failed["unregistered_output"] = "output/unsafe.zip"

    with pytest.raises(ValidationError):
        JobResult.model_validate(failed)


def test_cancelled_result_allows_an_optional_stable_error(
    successful_result: dict[str, Any]
) -> None:
    cancelled = deepcopy(successful_result)
    cancelled["status"] = "CANCELLED"
    cancelled["files"] = []
    cancelled["error"] = {
        "type": "PluginCancelledError",
        "code": "CANCELLED",
        "message": "Cancellation requested",
    }

    assert JobResult.model_validate(cancelled).error is not None


def test_cancelled_result_allows_no_error(successful_result: dict[str, Any]) -> None:
    cancelled = deepcopy(successful_result)
    cancelled["status"] = "CANCELLED"
    cancelled["files"] = []
    cancelled["error"] = None

    assert JobResult.model_validate(cancelled).error is None
