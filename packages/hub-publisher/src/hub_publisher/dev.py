"""Local trial runs for plugin projects (``hub-plugin dev``).

The subcommand synthesizes a one-shot Job workspace in a temporary directory
(``input``/``work``/``output``/``logs`` plus a generated ``job.json``), drives the
plugin entrypoint through the real runner protocol, and validates the written
``result.json`` against the job protocol contract — no running Hub required.

Protocol sources mirrored here (conflicts resolve to those sources):

- ``packages/hub-runner/src/hub_runner/execution.py`` — the runner CLI and how it
  resolves job directories (paths inside ``job.json``, relative to the job root);
- ``packages/hub-server/src/hub_server/services/workspaces.py`` — how the Hub
  stages inputs, fills parameter defaults and writes ``job.json``;
- ``packages/hub-runner/src/hub_runner/docker_executor.py`` — the docker job
  container constraints (network none, non-root, read-only rootfs, mounts).

Limitation: conda-pack runtimes are not emulated. A conda-only project is refused
with a readable message unless ``--mode python`` is requested explicitly.

The programmatic core behind the CLI is :func:`run_plugin_job`: it stages the
same workspace, executes the same runner subprocess and parses the same
``result.json``, returning a structured outcome instead of printing a report.
The plugin conformance harness (``tests/plugins/harness.py``) builds on it
instead of re-implementing the orchestration.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from python_hub_contracts import (
    RUNNER_EVENT_PREFIX,
    InputSpec,
    JobResult,
    JobRuntimeSpec,
    JobStatus,
    ParameterSpec,
    PluginManifest,
    RuntimeDirectories,
    RuntimeExecution,
    RuntimeInputFile,
    RuntimeJob,
    RuntimePlugin,
)

from .archive import sha256_file
from .builder import _build_hub_wheels, _run_command
from .project import PluginProject

_LOG_TAIL_LINES = 20
_INPUT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]*")
_CONTAINER_MOUNTS = (
    ("input", "/job/input", "ro"),
    ("job.json", "/job/job.json", "ro"),
    ("work", "/job/work", "rw"),
    ("output", "/job/output", "rw"),
    ("logs", "/job/logs", "rw"),
)


class DevError(Exception):
    """A readable pre-flight or protocol failure reported on stderr."""


@dataclass(frozen=True, slots=True)
class _Workspace:
    """The staged temporary job layout consumed by one dev run."""

    root: Path
    job_path: Path
    result_path: Path
    logs_dir: Path


def run_dev(
    *,
    project_dir: Path,
    inputs: list[str],
    params: list[str],
    mode: str | None,
    keep: bool,
) -> int:
    """Run one plugin project against a synthesized local job; return the exit code."""
    try:
        project = PluginProject.load(project_dir)
    except Exception as error:
        print(f"dev: {error}", file=sys.stderr)
        return 1
    try:
        selected_mode = _resolve_mode(project, mode)
        resolved_params = _resolve_params(project.manifest, params)
        planned_inputs = _plan_inputs(project.manifest, inputs)
    except DevError as error:
        print(f"dev: {error}", file=sys.stderr)
        return 1

    timeout = project.manifest.execution.timeout
    required_env = project.manifest.environment_variables.required
    if required_env:
        names = ", ".join(required_env)
        print(
            f"dev: warning: manifest 声明了必需环境变量({names}),"
            "本工具无法为其提供真实值",
            file=sys.stderr,
        )

    temporary = Path(tempfile.mkdtemp(prefix="hub-plugin-dev-"))
    job_id = f"dev-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    print(f"workspace: {temporary}")
    print(f"job: {job_id}")
    print(f"mode: {selected_mode}")
    try:
        exit_code = _run_in_workspace(
            project=project,
            temporary=temporary,
            job_id=job_id,
            selected_mode=selected_mode,
            timeout=timeout,
            resolved_params=resolved_params,
            planned_inputs=planned_inputs,
        )
    except DevError as error:
        print(f"dev: {error}", file=sys.stderr)
        exit_code = 1
    except subprocess.TimeoutExpired:
        print(
            f"dev: 作业超时:超过 manifest execution.timeout={timeout} 秒,进程已终止",
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        if keep:
            print(f"workspace kept: {temporary}")
        else:
            shutil.rmtree(temporary, ignore_errors=True)
    return exit_code


@dataclass(frozen=True, slots=True)
class LocalJobOutcome:
    """Structured outcome of one local plugin trial run (``run_plugin_job``)."""

    workspace: Path
    job_id: str
    #: 0 only when result.json was valid and the job reported SUCCESS.
    exit_code: int
    #: The parsed result.json, or ``None`` when it was missing or invalid.
    result: JobResult | None
    #: Why result.json was missing/invalid, or why the run timed out.
    contract_error: str | None
    runner_stdout: str = ""
    runner_stderr: str = ""


def run_plugin_job(
    *,
    project: PluginProject,
    workspace_root: Path,
    params: dict[str, object],
    inputs: list[Path | str],
) -> LocalJobOutcome:
    """Run one plugin job through the real runner protocol in python mode.

    The programmatic core behind ``hub-plugin dev``: it stages the same synthetic
    job workspace, executes ``python -m hub_runner`` against the project sources
    and parses the written ``result.json``. Docker/conda runtime execution is not
    provided here; runtime-level output comparison stays with the integration
    suite (``tests/integration/test_dual_runtime_nc_to_shp.py``).

    ``workspace_root`` must be an existing empty directory and is kept for
    inspection (the caller owns its lifetime). ``inputs`` are staged through the
    manifest's input declaration exactly like ``--input`` arguments;
    ``params`` must already carry typed values — the Hub's default filling and
    ``NAME=VALUE`` coercion are CLI concerns.
    """
    planned_inputs = _plan_inputs(project.manifest, [str(item) for item in inputs])
    job_id = f"dev-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    workspace = _stage_workspace(workspace_root, project, job_id, params, planned_inputs)
    timeout = project.manifest.execution.timeout
    stdout = stderr = ""
    try:
        returncode, stdout, stderr = _execute_python(
            project=project, workspace=workspace, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return LocalJobOutcome(
            workspace=workspace_root,
            job_id=job_id,
            exit_code=1,
            result=None,
            contract_error=(
                f"作业超时:超过 manifest execution.timeout={timeout} 秒,进程已终止"
            ),
            runner_stdout=stdout,
            runner_stderr=stderr,
        )
    result, contract_error = _read_result(workspace)
    failed = (
        contract_error is not None
        or result is None
        or returncode != 0
        or result.status is not JobStatus.SUCCESS
    )
    exit_code = 1 if failed else 0
    return LocalJobOutcome(
        workspace=workspace_root,
        job_id=job_id,
        exit_code=exit_code,
        result=result,
        contract_error=contract_error,
        runner_stdout=stdout,
        runner_stderr=stderr,
    )


def _run_in_workspace(
    *,
    project: PluginProject,
    temporary: Path,
    job_id: str,
    selected_mode: str,
    timeout: int,
    resolved_params: dict[str, object],
    planned_inputs: dict[str, list[Path]],
) -> int:
    workspace = _stage_workspace(
        temporary, project, job_id, resolved_params, planned_inputs
    )
    if selected_mode == "docker":
        dockerfile = project.dockerfile
        if dockerfile is None:  # pragma: no cover - _resolve_mode already refused
            raise DevError("--mode docker 需要项目提供 Dockerfile")
        returncode, stdout, stderr = _execute_docker(
            project=project,
            dockerfile=dockerfile,
            workspace=workspace,
            job_id=job_id,
            timeout=timeout,
        )
    else:
        returncode, stdout, stderr = _execute_python(
            project=project, workspace=workspace, timeout=timeout
        )
    _forward_output(stdout)
    if stderr.strip():
        print(stderr.rstrip(), file=sys.stderr)
    return _finish(workspace, returncode)


def _resolve_mode(project: PluginProject, mode: str | None) -> str:
    """Resolve ``--mode`` against the project runtimes and host capabilities."""
    selected = mode or "python"
    if selected == "docker":
        if project.dockerfile is None:
            raise DevError(
                "--mode docker 需要项目提供 Dockerfile(根目录或 docker/ 目录下)"
            )
        if shutil.which("docker") is None:
            raise DevError(
                "--mode docker 需要本机 Docker CLI 与 daemon(未在 PATH 中找到 docker)"
            )
        return selected
    if mode is None and project.conda_environment is not None and project.dockerfile is None:
        conda = "本机已检出 conda" if shutil.which("conda") is not None else "本机未检出 conda"
        raise DevError(
            "本项目只提供 conda 运行时(environment.yml),本工具不支持 conda 模式"
            f"({conda})。请用 --mode python 直接驱动 entrypoint,"
            "或在 Linux 构建后经 Hub 安装运行"
        )
    if importlib.util.find_spec("hub_runner") is None:
        raise DevError(
            "--mode python 需要 hub_runner 可导入"
            "(仓库 .venv 中已随 pip install -e packages/hub-runner 提供)"
        )
    return selected


def _resolve_params(manifest: PluginManifest, raw_params: list[str]) -> dict[str, object]:
    """Coerce ``NAME=VALUE`` arguments to manifest types and fill declared defaults."""
    parsed: dict[str, str] = {}
    for item in raw_params:
        name, separator, value = item.partition("=")
        if not separator or not _INPUT_NAME_PATTERN.fullmatch(name):
            raise DevError(f"--param 需要 NAME=VALUE 形式: {item!r}")
        if name in parsed:
            raise DevError(f"插件参数重复: {name}")
        parsed[name] = value
    declared = {parameter.name: parameter for parameter in manifest.parameters}
    unknown = sorted(set(parsed) - set(declared))
    if unknown:
        raise DevError(f"未声明的插件参数: {', '.join(unknown)}")

    resolved: dict[str, object] = {}
    for parameter in manifest.parameters:
        if parameter.name not in parsed:
            if parameter.default is not None:
                resolved[parameter.name] = parameter.default
            elif parameter.required:
                raise DevError(f"缺少必填插件参数: {parameter.name}")
            continue
        coerced = _coerce_param(parameter, parsed[parameter.name])
        _check_param_value(parameter, coerced)
        resolved[parameter.name] = coerced
    return resolved


def _coerce_param(parameter: ParameterSpec, raw: str) -> object:
    if parameter.type == "integer":
        try:
            return int(raw)
        except ValueError:
            raise DevError(f"插件参数 {parameter.name} 需要整数: {raw!r}") from None
    if parameter.type == "number":
        try:
            return float(raw)
        except ValueError:
            raise DevError(f"插件参数 {parameter.name} 需要数字: {raw!r}") from None
    if parameter.type == "boolean":
        lowered = raw.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        raise DevError(f"插件参数 {parameter.name} 需要 true/false: {raw!r}")
    if parameter.type == "string_list":
        values = [item.strip() for item in raw.split(",") if item.strip()]
        if not values:
            raise DevError(f"插件参数 {parameter.name} 不能是空列表")
        return values
    return raw


def _check_param_value(parameter: ParameterSpec, value: object) -> None:
    """Mirror the Hub's server-side parameter checks for coerced CLI values."""
    name = parameter.name
    if parameter.type == "enum" and value not in (parameter.options or []):
        raise DevError(f"插件参数 {name} 不在枚举选项内: {value!r}")
    if parameter.type == "string_list":
        values = cast("list[str]", value)
        if len(values) != len(set(values)):
            raise DevError(f"插件参数 {name} 不能包含重复值")
        if parameter.options is not None and any(
            item not in parameter.options for item in values
        ):
            raise DevError(f"插件参数 {name} 包含未声明的选项值")
        if parameter.min is not None and len(values) < parameter.min:
            raise DevError(f"插件参数 {name} 的列表长度不足(最少 {parameter.min})")
        if parameter.max is not None and len(values) > parameter.max:
            raise DevError(f"插件参数 {name} 的列表长度超出(最多 {parameter.max})")
    if isinstance(value, int | float) and not isinstance(value, bool):
        if parameter.min is not None and value < parameter.min:
            raise DevError(f"插件参数 {name} 小于最小值 {parameter.min}")
        if parameter.max is not None and value > parameter.max:
            raise DevError(f"插件参数 {name} 大于最大值 {parameter.max}")


def _plan_inputs(manifest: PluginManifest, raw_inputs: list[str]) -> dict[str, list[Path]]:
    """Map ``--input`` arguments onto manifest inputs with the Hub's count rules."""
    declared = {item.name: item for item in manifest.inputs}
    planned: dict[str, list[Path]] = {}
    for item in raw_inputs:
        name, source = _split_input_argument(manifest, declared, item)
        planned.setdefault(name, []).append(source)
    for spec in manifest.inputs:
        sources = planned.get(spec.name, [])
        if not sources:
            if spec.required:
                raise DevError(f"缺少必填插件输入: {spec.name}")
            continue
        if spec.type == "file" and len(sources) != 1:
            raise DevError(f"插件输入 {spec.name} 需要单个文件,收到 {len(sources)} 个")
        if spec.min_count is not None and len(sources) < spec.min_count:
            raise DevError(f"插件输入 {spec.name} 文件数量不足(最少 {spec.min_count})")
        if spec.max_count is not None and len(sources) > spec.max_count:
            raise DevError(f"插件输入 {spec.name} 文件数量超出(最多 {spec.max_count})")
        for source in sources:
            if not source.is_file():
                raise DevError(f"输入文件不存在: {source}")
            if spec.max_size is not None and source.stat().st_size > spec.max_size:
                raise DevError(
                    f"输入文件 {source.name} 超过 {spec.name} 的大小上限 "
                    f"{spec.max_size} 字节"
                )
    return planned


def _split_input_argument(
    manifest: PluginManifest,
    declared: dict[str, InputSpec],
    item: str,
) -> tuple[str, Path]:
    """Resolve one ``--input`` value to a declared input name, matching by extension."""
    name: str | None = None
    candidate = item
    if "=" in item:
        left, right = item.split("=", 1)
        if _INPUT_NAME_PATTERN.fullmatch(left):
            name, candidate = left, right
    source = Path(candidate)
    if not source.is_file():
        raise DevError(f"输入文件不存在: {item}")
    if name is not None:
        if name not in declared:
            raise DevError(f"未声明的插件输入: {name}")
        return name, source
    suffix = source.suffix.lower()
    matches = [
        spec
        for spec in manifest.inputs
        if not spec.extensions or suffix in {item.lower() for item in spec.extensions}
    ]
    if not matches:
        if not manifest.inputs:
            raise DevError(f"manifest 未声明任何输入, 无法接收 --input: {source.name}")
        details = "; ".join(
            f"{spec.name}: {','.join(spec.extensions) or '任意扩展名'}"
            for spec in manifest.inputs
        )
        raise DevError(
            f"输入 {source.name} 的扩展名 {suffix or '(无)'} "
            f"不符合任何声明输入的扩展名要求({details})"
        )
    if len(matches) > 1:
        names = ", ".join(spec.name for spec in matches)
        raise DevError(
            f"输入 {source.name} 的扩展名同时匹配多个声明输入({names}),"
            "请用 --input NAME=FILE 显式指定"
        )
    return matches[0].name, source


def _stage_workspace(
    temporary: Path,
    project: PluginProject,
    job_id: str,
    resolved_params: dict[str, object],
    planned_inputs: dict[str, list[Path]],
) -> _Workspace:
    """Create the job directory layout and the synthetic ``job.json``."""
    for child in ("input", "work", "output", "logs"):
        (temporary / child).mkdir()
    runtime_inputs = _copy_inputs(temporary / "input", project.manifest, planned_inputs)
    spec = JobRuntimeSpec(
        protocol_version="1.0",
        job=RuntimeJob(id=job_id, created_at=datetime.now(UTC)),
        plugin=RuntimePlugin(
            id=project.manifest.plugin.id,
            version=project.manifest.plugin.version,
            build_id="dev-local",
        ),
        params=resolved_params,
        inputs=runtime_inputs,
        directories=RuntimeDirectories(input="input", work="work", output="output", logs="logs"),
        execution=RuntimeExecution(timeout=project.manifest.execution.timeout),
    )
    job_path = temporary / "job.json"
    job_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    return _Workspace(
        root=temporary,
        job_path=job_path,
        result_path=temporary / "output" / "result.json",
        logs_dir=temporary / "logs",
    )


def _copy_inputs(
    input_dir: Path,
    manifest: PluginManifest,
    planned_inputs: dict[str, list[Path]],
) -> dict[str, RuntimeInputFile | list[RuntimeInputFile]]:
    """Copy staged inputs and describe them with the Hub's runtime input contract."""
    runtime_inputs: dict[str, RuntimeInputFile | list[RuntimeInputFile]] = {}
    index = 0
    for spec in manifest.inputs:
        sources = planned_inputs.get(spec.name)
        if not sources:
            continue
        entries: list[RuntimeInputFile] = []
        for source in sources:
            destination = input_dir / source.name
            if destination.exists():
                raise DevError(f"插件输入 {spec.name} 的文件名冲突: {source.name}")
            shutil.copyfile(source, destination)
            extension = destination.suffix.lower()
            if not extension:
                raise DevError(f"输入文件必须有扩展名: {source.name}")
            index += 1
            entries.append(
                RuntimeInputFile(
                    id=f"dev-file-{index:03d}",
                    name=destination.name,
                    path=f"input/{destination.name}",
                    size=destination.stat().st_size,
                    extension=extension,
                    sha256=sha256_file(destination),
                )
            )
        runtime_inputs[spec.name] = entries if spec.type == "files" else entries[0]
    return runtime_inputs


def _execute_python(
    *,
    project: PluginProject,
    workspace: _Workspace,
    timeout: int,
) -> tuple[int, str, str]:
    """Drive ``python -m hub_runner`` with the project's ``src`` as plugin root."""
    command = [
        sys.executable,
        "-m",
        "hub_runner",
        "--job",
        str(workspace.job_path),
        "--manifest",
        str(project.manifest_path),
        "--plugin-root",
        str(project.source_dir),
        "--result",
        str(workspace.result_path),
    ]
    env = {
        **os.environ,
        "HUB_INPUT_DIR": str(workspace.root / "input"),
        "HUB_WORK_DIR": str(workspace.root / "work"),
        "HUB_OUTPUT_DIR": str(workspace.root / "output"),
        "HUB_LOG_DIR": str(workspace.root / "logs"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
    }
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        env=env,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, completed.stdout, completed.stderr


def _execute_docker(
    *,
    project: PluginProject,
    dockerfile: Path,
    workspace: _Workspace,
    job_id: str,
    timeout: int,
) -> tuple[int, str, str]:
    """Build and run the plugin image with docker_executor's job container rules."""
    context = workspace.root / "docker-context"
    context.mkdir()
    try:
        _build_hub_wheels(workspace.root / "wheelhouse", _run_command)
        shutil.copytree(project.source_dir, context / "plugin")
        shutil.copy2(project.manifest_path, context / "plugin.yaml")
        shutil.copy2(dockerfile, context / "Dockerfile")
        image = f"{project.manifest.plugin.id}:dev"
        _run_command(
            [
                "docker",
                "build",
                "--build-arg",
                f"SOURCE_SHA256={project.source_digest()}",
                "--tag",
                image,
                str(context),
            ]
        )
    except subprocess.CalledProcessError as error:
        raise DevError(
            f"docker 构建失败(退出码 {error.returncode}),请检查上方 docker 输出"
        ) from None
    except OSError as error:
        raise DevError(f"docker 构建无法启动: {error}") from None

    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        f"hub-plugin-dev-{job_id}",
        "--network",
        "none",
        "--user",
        "65532:65532",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--env",
        "HUB_INPUT_DIR=/job/input",
        "--env",
        "HUB_WORK_DIR=/job/work",
        "--env",
        "HUB_OUTPUT_DIR=/job/output",
        "--env",
        "HUB_LOG_DIR=/job/logs",
        "--env",
        "PYTHONUNBUFFERED=1",
    ]
    for host_name, container_path, mode in _CONTAINER_MOUNTS:
        command += [
            "--volume",
            f"{_bind_path(workspace.root / host_name)}:{container_path}:{mode}",
        ]
    command += [
        image,
        "--job",
        "/job/job.json",
        "--manifest",
        "/plugin/plugin.yaml",
        "--plugin-root",
        "/plugin",
        "--result",
        "/job/output/result.json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        _force_remove_container(f"hub-plugin-dev-{job_id}")
        raise
    return completed.returncode, completed.stdout, completed.stderr


def _force_remove_container(name: str) -> None:
    """Best-effort cleanup so a killed client does not orphan the job container."""
    with suppress(Exception):
        subprocess.run(
            ["docker", "rm", "-f", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )


def _bind_path(path: Path) -> str:
    """Format a host path for ``docker run --volume`` (Docker Desktop friendly)."""
    return path.resolve().as_posix()


def _read_result(workspace: _Workspace) -> tuple[JobResult | None, str | None]:
    """Parse ``output/result.json`` against the job protocol contract.

    Returns ``(result, None)`` on success and ``(None, problem)`` when the file
    is missing or violates the contract; shared by the CLI report path and
    :func:`run_plugin_job`.
    """
    if not workspace.result_path.is_file():
        return None, f"result.json 缺失(作业未执行到写结果阶段): {workspace.result_path}"
    try:
        return JobResult.model_validate_json(workspace.result_path.read_text("utf-8")), None
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        return None, f"result.json 契约校验失败: {details}"


def _finish(workspace: _Workspace, returncode: int) -> int:
    """Validate ``result.json`` against the contract and print the run report."""
    result, problem = _read_result(workspace)
    if problem is not None or result is None:
        print(f"dev: {problem}", file=sys.stderr)
        _print_log_tail(workspace.logs_dir)
        return 1
    _report(result, workspace.logs_dir)
    if returncode != 0 or result.status is not JobStatus.SUCCESS:
        if result.error is not None:
            print(
                "dev: 作业未成功: "
                f"code={result.error.code} type={result.error.type} "
                f"message={result.error.message}",
                file=sys.stderr,
            )
        else:
            print(
                f"dev: 作业未成功: status={result.status.value} 退出码={returncode}",
                file=sys.stderr,
            )
        return 1
    return 0


def _report(result: JobResult, logs_dir: Path) -> None:
    print(f"status: {result.status.value} ({result.duration_ms} ms)")
    print(f"message: {result.message}")
    if result.data:
        print(f"data: {json.dumps(result.data, ensure_ascii=False, sort_keys=True)}")
    if result.files:
        print("outputs:")
        for item in result.files:
            print(f"  - {item.path}  size={item.size}  sha256={item.sha256[:12]}")
    if result.error is not None:
        print(
            f"error: code={result.error.code} type={result.error.type} "
            f"message={result.error.message}"
        )
    print("log tail (logs/runner.log):")
    for line in _log_tail(logs_dir / "runner.log"):
        print(f"  {line}")


def _log_tail(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text("utf-8", errors="replace").splitlines()
    return lines[-_LOG_TAIL_LINES:]


def _print_log_tail(logs_dir: Path) -> None:
    print("log tail (logs/runner.log):", file=sys.stderr)
    for line in _log_tail(logs_dir / "runner.log"):
        print(f"  {line}", file=sys.stderr)


def _forward_output(raw: str) -> None:
    """Print the runner's captured output, hiding machine-readable event lines."""
    for line in raw.splitlines():
        if line.startswith(RUNNER_EVENT_PREFIX):
            continue
        print(line)
