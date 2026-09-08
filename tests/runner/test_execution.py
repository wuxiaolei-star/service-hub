from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from hub_runner.execution import run_job
from python_hub_contracts import JobResult, JobStatus


def test_runner_loads_entrypoint_and_writes_success_result(tmp_path: Path) -> None:
    """Missing manifest entrypoint loading prevents real plugins from running."""
    job_path, manifest_path, plugin_root, result_path = _write_job_fixture(tmp_path)
    plugin_file = plugin_root / "src" / "main.py"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text(
        "\n".join(
            [
                "from python_hub_sdk import OutputFile, PluginResult",
                "",
                "def run(params, inputs, context):",
                "    context.progress(50, 'halfway')",
                "    source = inputs['source_nc']",
                "    assert source.path == 'input/source.nc'",
                "    output = context.output_file('answer.txt')",
                "    output.write_text(str(params['value']), encoding='utf-8')",
                "    return PluginResult(",
                "        message='plugin complete',",
                "        data={'input_name': source.name},",
                "        files=[",
                "            OutputFile(",
                "                name='answer.txt',",
                "                path='answer.txt',",
                "                format='text/plain',",
                "            )",
                "        ],",
                "    )",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = run_job(
        job_path=job_path,
        manifest_path=manifest_path,
        plugin_root=plugin_root,
        result_path=result_path,
    )

    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    assert exit_code == 0
    assert result.status is JobStatus.SUCCESS
    assert result.message == "plugin complete"
    assert result.data == {"input_name": "source.nc"}
    assert result.files[0].path == "answer.txt"
    assert (
        result.files[0].sha256
        == "73475cb40a568e8da8a045ced110137e159f890ac4da883b6b17dc651b3a8049"
    )


def test_runner_turns_plugin_validation_error_into_failed_result(tmp_path: Path) -> None:
    """Collapsing SDK validation errors would lose the stable plugin error code."""
    job_path, manifest_path, plugin_root, result_path = _write_job_fixture(tmp_path)
    plugin_file = plugin_root / "src" / "main.py"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text(
        "\n".join(
            [
                "from python_hub_sdk import PluginValidationError",
                "",
                "def run(params, inputs, context):",
                "    raise PluginValidationError(",
                "        code='NC_GROUP_NOT_FOUND',",
                "        message='Group not found',",
                "        details={'group': params['group_name']},",
                "    )",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = run_job(
        job_path=job_path,
        manifest_path=manifest_path,
        plugin_root=plugin_root,
        result_path=result_path,
    )

    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    assert exit_code != 0
    assert result.status is JobStatus.FAILED
    assert result.error is not None
    assert result.error.type == "PluginValidationError"
    assert result.error.code == "NC_GROUP_NOT_FOUND"
    assert result.error.details == {"group": "missing"}


def test_runner_writes_unexpected_tracebacks_to_runner_log_only(tmp_path: Path) -> None:
    """Unexpected plugin tracebacks must not be serialized into public result.json."""
    job_path, manifest_path, plugin_root, result_path = _write_job_fixture(tmp_path)
    plugin_file = plugin_root / "src" / "main.py"
    plugin_file.parent.mkdir(parents=True)
    plugin_file.write_text(
        "\n".join(
            [
                "def run(params, inputs, context):",
                "    raise RuntimeError('database password: secret')",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = run_job(
        job_path=job_path,
        manifest_path=manifest_path,
        plugin_root=plugin_root,
        result_path=result_path,
    )

    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    runner_log = tmp_path / "logs" / "runner.log"
    assert exit_code != 0
    assert result.error is not None
    assert result.error.code == "PLUGIN_UNEXPECTED_ERROR"
    assert "secret" not in result.error.message
    assert "database password: secret" in runner_log.read_text("utf-8")


def _write_job_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    job_path = tmp_path / "job.json"
    manifest_path = tmp_path / "plugin.yaml"
    plugin_root = tmp_path / "plugin"
    result_path = tmp_path / "result.json"
    (tmp_path / "input").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "input" / "source.nc").write_bytes(b"abc")
    manifest_path.write_text(
        "\n".join(
            [
                'spec_version: "1.0"',
                "plugin:",
                "  id: nc_to_shp",
                "  name: NC转Shapefile",
                '  version: "1.0.0"',
                "  description: test",
                "sdk:",
                '  version: "1.0"',
                "runtime:",
                "  type: process",
                "  python:",
                '    version: "3.10"',
                "entrypoint:",
                "  module: src.main",
                "  function: run",
                "inputs:",
                "  - name: source_nc",
                "    label: Source NC",
                "    type: file",
                "    required: true",
                "    extensions: [.nc]",
                "outputs:",
                "  - name: result",
                "    label: Result",
                "    type: file",
                "    required: true",
                "execution:",
                "  timeout: 60",
                "  concurrency: 1",
                "environment_variables:",
                "  required: []",
                "healthcheck:",
                "  enabled: false",
                "  type: import",
            ]
        ),
        encoding="utf-8",
    )
    job_path.write_text(
        json.dumps(
            {
                "protocol_version": "1.0",
                "job": {
                    "id": "job_123",
                    "created_at": datetime(2026, 9, 6, 0, 0, tzinfo=UTC).isoformat(),
                },
                "plugin": {
                    "id": "nc_to_shp",
                    "version": "1.0.0",
                    "build_id": "build_123",
                },
                "params": {"value": 42, "group_name": "missing"},
                "inputs": {
                    "source_nc": {
                        "id": "file_123",
                        "name": "source.nc",
                        "path": "input/source.nc",
                        "size": 3,
                        "extension": ".nc",
                        "sha256": (
                            "ba7816bf8f01cfea414140de5dae2223"
                            "b00361a396177a9cb410ff61f20015ad"
                        ),
                    }
                },
                "directories": {
                    "input": "input",
                    "work": "work",
                    "output": "output",
                    "logs": "logs",
                },
                "execution": {"timeout": 60},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_path, manifest_path, plugin_root, result_path
