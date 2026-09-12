from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hub_publisher.project import PluginProject
from hub_runner.execution import run_job
from python_hub_contracts import JobResult, JobStatus


@pytest.mark.parametrize(
    ("template", "runtime"),
    [
        ("conda-script-plugin", "conda-pack"),
        ("docker-job-plugin", "docker"),
    ],
)
def test_template_is_valid_plugin_project(template: str, runtime: str) -> None:
    """A template that fails project validation would produce unbuildable plugins."""
    project = PluginProject.load(Path("templates") / template)
    project.validate_runtime(runtime)
    assert project.manifest.entrypoint.function == "run"


@pytest.mark.parametrize("template", ["conda-script-plugin", "docker-job-plugin"])
def test_template_entrypoint_runs_and_writes_result(tmp_path: Path, template: str) -> None:
    """Template code that cannot execute end to end would mislead new plugin authors."""
    template_root = Path("templates") / template
    job_path, manifest_path, plugin_root, result_path = _write_job_fixture(
        tmp_path, template_root
    )

    exit_code = run_job(
        job_path=job_path,
        manifest_path=manifest_path,
        plugin_root=plugin_root,
        result_path=result_path,
    )

    result = JobResult.model_validate_json(result_path.read_text("utf-8"))
    output = tmp_path / "output" / "result.txt"
    assert exit_code == 0
    assert result.status is JobStatus.SUCCESS
    assert result.message == "处理完成"
    assert result.files[0].path == "result.txt"
    assert output.read_text("utf-8") == "hello from template\n"


def _write_job_fixture(
    tmp_path: Path, template_root: Path
) -> tuple[Path, Path, Path, Path]:
    job_path = tmp_path / "job.json"
    manifest_path = tmp_path / "plugin.yaml"
    plugin_root = tmp_path / "plugin"
    result_path = tmp_path / "result.json"
    (tmp_path / "input").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "logs").mkdir()
    shutil.copytree(template_root / "src", plugin_root)
    manifest_path.write_text(
        (template_root / "plugin.yaml").read_text("utf-8"), encoding="utf-8"
    )
    job_path.write_text(
        json.dumps(
            {
                "protocol_version": "1.0",
                "job": {
                    "id": "job_123",
                    "created_at": datetime(2026, 9, 12, 0, 0, tzinfo=UTC).isoformat(),
                },
                "plugin": {
                    "id": "example_script",
                    "version": "1.0.0",
                    "build_id": "build_123",
                },
                "params": {"message": "hello from template"},
                "inputs": {},
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
