from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError
from python_hub_contracts import JobResult, JobRuntimeSpec, JobStatus

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
