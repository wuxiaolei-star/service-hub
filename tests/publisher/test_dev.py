"""Tests for the `hub-plugin dev` local trial-run subcommand.

The python-mode tests drive the real runner protocol through a subprocess
(``python -m hub_runner``), mirroring tests/runner/test_execution.py but from
outside: the CLI stages a synthetic job workspace, executes the plugin and
validates the written result.json against the job protocol contract.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from hub_publisher.cli import main

MANIFEST = """\
spec_version: "1.0"
plugin:
  id: copy_plugin
  name: Copy Plugin
  version: "1.0.0"
  description: dev 试跑用最小插件
sdk:
  version: "1.0"
runtime:
  type: process
  python:
    version: "3.12"
entrypoint:
  module: copy_plugin.main
  function: run
parameters:
  - name: message
    label: 消息
    type: string
    required: false
    default: hello
inputs:
  - name: source
    label: 源文件
    type: file
    required: true
    extensions: [.txt]
outputs:
  - name: result
    label: 结果文件
    type: file
    required: true
execution:
  timeout: 60
  concurrency: 1
environment_variables:
  required: []
healthcheck:
  enabled: true
  type: import
"""

COPY_PLUGIN = """\
from python_hub_sdk import InputFile, OutputFile, PluginContext, PluginResult


def run(params, inputs, context):
    source = inputs["source"]
    assert isinstance(source, InputFile)
    text = (context.input_dir / source.name).read_text(encoding="utf-8")
    destination = context.output_file("result.txt", create_parent=True)
    destination.write_text(str(params.get("message")) + text, encoding="utf-8")
    return PluginResult(
        message="copied",
        data={"message": params.get("message"), "source": source.name},
        files=[OutputFile(name="result", path="result.txt", format="text/plain")],
    )
"""

FAILING_PLUGIN = """\
from python_hub_sdk import PluginValidationError


def run(params, inputs, context):
    raise PluginValidationError(code="DEV_TEST_INVALID", message="bad input")
"""

# The runner owns result.json and rewrites it after the plugin returns, so the
# only faithful way for a plugin to leave a corrupted result behind is to clobber
# it in an atexit hook, which fires after the runner's final atomic write.
CORRUPTING_PLUGIN = """\
import atexit

from python_hub_sdk import PluginResult


def run(params, inputs, context):
    def corrupt():
        (context.output_dir / "result.json").write_text("{broken json", encoding="utf-8")

    atexit.register(corrupt)
    return PluginResult(message="ok")
"""


def _write_project(
    root: Path,
    *,
    main_source: str = COPY_PLUGIN,
    extra_files: dict[str, str] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    package = root / "src" / "copy_plugin"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "main.py").write_text(main_source, encoding="utf-8")
    for name, content in (extra_files or {}).items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _write_input(tmp_path: Path, name: str = "data.txt", content: str = "payload") -> Path:
    source = tmp_path / name
    source.write_text(content, encoding="utf-8")
    return source


def test_dev_runs_the_full_python_chain(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A template-style plugin runs end to end and its result passes the contract."""
    root = tmp_path / "plugin"
    _write_project(root)
    source = _write_input(tmp_path)

    exit_code = main(["dev", str(root), "--input", str(source), "--param", "message=hi"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "job: dev-" in output
    assert "mode: python" in output
    assert "status: SUCCESS" in output
    # The --param value was coerced and delivered, and the declared default of
    # unpassed parameters is filled exactly like the Hub does.
    assert '"message": "hi"' in output
    assert '"source": "data.txt"' in output
    assert "- result.txt" in output
    assert "sha256=" in output


def test_dev_reports_plugin_validation_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stable plugin error code must survive into the dev tool's stderr."""
    root = tmp_path / "plugin"
    _write_project(root, main_source=FAILING_PLUGIN)
    source = _write_input(tmp_path)

    exit_code = main(["dev", str(root), "--input", str(source)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "DEV_TEST_INVALID" in captured.err
    assert "status: FAILED" in captured.out


def test_dev_rejects_a_corrupted_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A plugin that clobbers result.json fails the contract validation."""
    root = tmp_path / "plugin"
    _write_project(root, main_source=CORRUPTING_PLUGIN)
    source = _write_input(tmp_path)

    exit_code = main(["dev", str(root), "--input", str(source)])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "result.json 契约校验失败" in stderr


def test_dev_rejects_inputs_outside_the_declared_extensions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Extension mismatches are caught before any job workspace is executed."""
    root = tmp_path / "plugin"
    _write_project(root)
    source = _write_input(tmp_path, name="data.nc")

    exit_code = main(["dev", str(root), "--input", str(source)])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert ".nc" in stderr
    assert "source: .txt" in stderr


def test_dev_reports_a_missing_docker_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing Docker CLI fails with a readable message instead of a traceback."""
    root = tmp_path / "plugin"
    _write_project(root, extra_files={"Dockerfile": "FROM python:3.12-slim\n"})
    monkeypatch.setattr(shutil, "which", lambda name: None)

    exit_code = main(["dev", str(root), "--mode", "docker"])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "docker" in stderr
    assert "PATH" in stderr


def test_dev_refuses_conda_only_projects_without_conda(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Conda-only projects get the conda-mode refusal with actionable hints."""
    root = tmp_path / "plugin"
    _write_project(root, extra_files={"environment.yml": "name: copy\n"})
    monkeypatch.setattr(shutil, "which", lambda name: None)
    source = _write_input(tmp_path)

    exit_code = main(["dev", str(root), "--input", str(source)])

    stderr = capsys.readouterr().err
    assert exit_code == 1
    assert "本工具不支持 conda 模式" in stderr
    assert "--mode python" in stderr
