from pathlib import Path

from python_hub_contracts import (
    JobResult,
    JobRuntimeSpec,
    JobStatus,
    PluginBuildManifest,
    ProgressEvent,
    load_plugin_manifest,
    parse_runner_line,
)
from python_hub_sdk import OutputFile, PluginResult

FIXTURES = Path(__file__).parent / "fixtures"
SHA256 = "a" * 64


def test_public_contracts_form_one_lossless_execution_chain() -> None:
    plugin = load_plugin_manifest(FIXTURES / "valid-plugin.yaml")
    build = PluginBuildManifest.model_validate_json(
        (FIXTURES / "valid-build.json").read_text(encoding="utf-8")
    )
    build.assert_matches_plugin(plugin)

    runtime = JobRuntimeSpec.model_validate(
        {
            "protocol_version": "1.0",
            "job": {
                "id": "job_01K123",
                "created_at": "2026-09-04T14:30:00+08:00",
            },
            "plugin": {
                "id": build.plugin_id,
                "version": build.plugin_version,
                "build_id": build.build_id,
            },
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
            "directories": {
                "input": "input",
                "work": "work",
                "output": "output",
                "logs": "logs",
            },
            "execution": {"timeout": plugin.execution.timeout},
        }
    )
    restored_runtime = JobRuntimeSpec.model_validate_json(runtime.model_dump_json())

    plugin_result = PluginResult(
        message="NC转换完成",
        data={"time_count": 24, "point_count": 158624},
        files=[OutputFile(name="水深结果", path="depth.zip")],
    )
    output = plugin_result.files[0]
    job_result = JobResult.model_validate(
        {
            "protocol_version": restored_runtime.protocol_version,
            "job_id": restored_runtime.job.id,
            "status": JobStatus.SUCCESS,
            "started_at": "2026-09-04T14:30:05+08:00",
            "finished_at": "2026-09-04T14:31:28+08:00",
            "duration_ms": 83000,
            "message": plugin_result.message,
            "data": plugin_result.data,
            "files": [output.to_protocol_dict() | {"size": 5823674, "sha256": SHA256}],
            "error": None,
        }
    )
    event = parse_runner_line(
        '@@HUB@@{"protocol_version":"1.0","type":"progress",'
        '"percent":50,"message":"正在生成Shapefile"}'
    )

    assert build.plugin_id == plugin.plugin.id == restored_runtime.plugin.id == "nc_to_shp"
    assert (
        build.plugin_version
        == plugin.plugin.version
        == restored_runtime.plugin.version
        == "1.0.0"
    )
    assert build.runtime.environment_path == "runtime/env.tar.zst"
    assert restored_runtime.inputs["nc_file"].path == "input/model.nc"
    assert job_result.job_id == restored_runtime.job.id == "job_01K123"
    assert job_result.files[0].path == output.path == "depth.zip"
    assert job_result.files[0].format is output.format is None
    assert job_result.status is JobStatus.SUCCESS
    assert isinstance(event, ProgressEvent)
    assert (
        event.protocol_version
        == restored_runtime.protocol_version
        == job_result.protocol_version
    )
    assert event.percent == 50
