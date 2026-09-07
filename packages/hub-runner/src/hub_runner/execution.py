from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import traceback
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from python_hub_contracts import (
    RUNNER_EVENT_PREFIX,
    JobError,
    JobResult,
    JobRuntimeSpec,
    JobStatus,
    LogEvent,
    LogLevel,
    ProgressEvent,
    RuntimeInputFile,
    RuntimeInputValue,
    RuntimeOutputFile,
    load_plugin_manifest,
)
from python_hub_sdk import (
    InputFile,
    PluginCancelledError,
    PluginContext,
    PluginError,
    PluginResult,
)

from .result_writer import write_result_atomic

PluginEntrypoint = Callable[
    [PluginContext, Mapping[str, InputFile | list[InputFile]], Mapping[str, Any]],
    PluginResult | None,
]


def run_job(
    *,
    job_path: Path,
    manifest_path: Path,
    plugin_root: Path,
    result_path: Path,
) -> int:
    """Run one manifest-declared plugin entrypoint and write a terminal result."""
    started_at = datetime.now(UTC)
    job_root = job_path.parent.resolve()
    logs_dir = _job_path(job_root, "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    runner_log = logs_dir / "runner.log"
    _emit_log(LogLevel.INFO, "job started")

    try:
        job = JobRuntimeSpec.model_validate_json(job_path.read_text("utf-8"))
        manifest = load_plugin_manifest(manifest_path)
        entrypoint = _load_entrypoint(
            plugin_root=plugin_root,
            module_name=manifest.entrypoint.module,
            function_name=manifest.entrypoint.function,
        )
        context = PluginContext(
            job_id=job.job.id,
            plugin_id=job.plugin.id,
            plugin_version=job.plugin.version,
            input_dir=_job_path(job_root, job.directories.input),
            work_dir=_job_path(job_root, job.directories.work),
            output_dir=_job_path(job_root, job.directories.output),
            logger=_RunnerLogger(runner_log),
            event_sink=_StdoutEventSink(),
            cancellation_probe=lambda: False,
        )
        sdk_inputs = {
            name: _to_sdk_input(value) for name, value in job.inputs.items()
        }
        plugin_result = entrypoint(context, sdk_inputs, job.params) or PluginResult()
        result = _success_result(
            job=job,
            plugin_result=plugin_result,
            output_dir=context.output_dir,
            started_at=started_at,
        )
        write_result_atomic(result_path, result)
        _emit_progress(100, "job completed")
        _emit_log(LogLevel.INFO, "job completed")
        return 0
    except PluginCancelledError as error:
        result = _error_result(
            job_id=_read_job_id(job_path),
            status=JobStatus.CANCELLED,
            started_at=started_at,
            error=JobError(
                type=error.error_type,
                code=error.code,
                message=error.message,
                details=error.details,
            ),
            message=error.message,
        )
    except PluginError as error:
        result = _error_result(
            job_id=_read_job_id(job_path),
            status=JobStatus.FAILED,
            started_at=started_at,
            error=JobError(
                type=error.error_type,
                code=error.code,
                message=error.message,
                details=error.details,
            ),
            message=error.message,
        )
    except Exception:
        _write_traceback(runner_log)
        result = _error_result(
            job_id=_read_job_id(job_path),
            status=JobStatus.FAILED,
            started_at=started_at,
            error=JobError(
                type="UnexpectedError",
                code="PLUGIN_UNEXPECTED_ERROR",
                message="插件执行出现未预期错误, 请查看 runner.log",
            ),
            message="plugin failed",
        )

    write_result_atomic(result_path, result)
    _emit_log(LogLevel.ERROR, result.message)
    return 1


def _load_entrypoint(
    *,
    plugin_root: Path,
    module_name: str,
    function_name: str,
) -> PluginEntrypoint:
    root = str(plugin_root.resolve())
    sys.path.insert(0, root)
    try:
        _drop_cached_module(module_name)
        module = importlib.import_module(module_name)
        entrypoint = getattr(module, function_name)
    finally:
        with suppress(ValueError):
            sys.path.remove(root)
    if not callable(entrypoint):
        raise TypeError("plugin entrypoint must be callable")
    return cast(PluginEntrypoint, entrypoint)


def _drop_cached_module(module_name: str) -> None:
    segments = module_name.split(".")
    for index in range(len(segments), 0, -1):
        sys.modules.pop(".".join(segments[:index]), None)


def _to_sdk_input(value: RuntimeInputValue) -> InputFile | list[InputFile]:
    if isinstance(value, list):
        return [_to_single_sdk_input(item) for item in value]
    return _to_single_sdk_input(value)


def _to_single_sdk_input(value: RuntimeInputFile) -> InputFile:
    return InputFile(
        id=value.id,
        name=value.name,
        path=value.path,
        size=value.size,
        extension=value.extension,
        sha256=value.sha256,
    )


def _success_result(
    *,
    job: JobRuntimeSpec,
    plugin_result: PluginResult,
    output_dir: Path,
    started_at: datetime,
) -> JobResult:
    finished_at = datetime.now(UTC)
    return JobResult(
        protocol_version="1.0",
        job_id=job.job.id,
        status=JobStatus.SUCCESS,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_duration_ms(started_at, finished_at),
        message=plugin_result.message or "success",
        data=plugin_result.data,
        files=[
            _runtime_output_file(output_dir=output_dir, output_file=output_file)
            for output_file in plugin_result.files
        ],
        error=None,
    )


def _runtime_output_file(output_dir: Path, output_file: Any) -> RuntimeOutputFile:
    path = output_file.path
    absolute = (output_dir / path).resolve(strict=True)
    try:
        absolute.relative_to(output_dir.resolve())
    except ValueError as error:
        raise ValueError("output file must remain inside output directory") from error
    return RuntimeOutputFile(
        name=output_file.name,
        path=path,
        format=output_file.format,
        size=absolute.stat().st_size,
        sha256=_sha256_file(absolute),
    )


def _error_result(
    *,
    job_id: str,
    status: Literal[JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.TIMED_OUT],
    started_at: datetime,
    error: JobError,
    message: str,
) -> JobResult:
    finished_at = datetime.now(UTC)
    return JobResult(
        protocol_version="1.0",
        job_id=job_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_duration_ms(started_at, finished_at),
        message=message,
        data={},
        files=[],
        error=error,
    )


def _job_path(job_root: Path, relative_path: str) -> Path:
    candidate = (job_root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(job_root)
    except ValueError as error:
        raise ValueError("job path must remain inside job root") from error
    return candidate


def _read_job_id(job_path: Path) -> str:
    try:
        payload = json.loads(job_path.read_text("utf-8"))
        job = payload.get("job")
        if isinstance(job, dict) and isinstance(job.get("id"), str) and job["id"]:
            return cast(str, job["id"])
    except Exception:
        pass
    return "unknown"


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(0, int((finished_at - started_at).total_seconds() * 1000))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _emit_progress(percent: int, message: str | None) -> None:
    event = ProgressEvent(protocol_version="1.0", type="progress", percent=percent, message=message)
    print(f"{RUNNER_EVENT_PREFIX}{event.model_dump_json()}", flush=True)


def _emit_log(level: LogLevel, message: str) -> None:
    event = LogEvent(protocol_version="1.0", type="log", level=level, message=message)
    print(f"{RUNNER_EVENT_PREFIX}{event.model_dump_json()}", flush=True)


def _write_traceback(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as output:
        output.write("".join(traceback.format_exc()))
        output.flush()
        os.fsync(output.fileno())


class _StdoutEventSink:
    def emit_progress(self, percent: int, message: str | None) -> None:
        _emit_progress(percent, message)


class _RunnerLogger:
    def __init__(self, path: Path) -> None:
        self._path = path

    def debug(self, msg: object, *args: object, **_: object) -> None:
        self._write(LogLevel.DEBUG, msg, args)

    def info(self, msg: object, *args: object, **_: object) -> None:
        self._write(LogLevel.INFO, msg, args)

    def warning(self, msg: object, *args: object, **_: object) -> None:
        self._write(LogLevel.WARNING, msg, args)

    def error(self, msg: object, *args: object, **_: object) -> None:
        self._write(LogLevel.ERROR, msg, args)

    def _write(self, level: LogLevel, msg: object, args: tuple[object, ...]) -> None:
        text = str(msg)
        if args:
            text = text % args
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(f"{level} {text}\n")
            output.flush()
            os.fsync(output.fileno())
        _emit_log(level, text)
