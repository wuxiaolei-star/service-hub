from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import zstandard
from python_hub_contracts import JobStatus, RunnerEvent, RuntimeType, parse_runner_line


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    termination_reason: Literal["cancelled", "timeout"] | None = None
    events_forwarded: bool = False


@dataclass(frozen=True, slots=True)
class RunnerBuild:
    id: str
    runtime_type: RuntimeType
    runtime_archive: str
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class InstallResult:
    status: Literal["SUCCESS", "FAILED"]
    environment_path: str | None = None
    metadata: dict[str, object] | None = None
    error_summary: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerJob:
    id: str
    runtime_type: RuntimeType
    workspace: str
    job_path: str
    manifest_path: str
    plugin_root: str
    result_path: str
    environment_path: str | None
    timeout_seconds: int
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class RunnerCompletion:
    status: JobStatus
    exit_code: int | None
    error_summary: str | None = None


CommandRunner = Callable[
    [Sequence[str]],
    CommandResult,
]
EventCallback = Callable[[RunnerEvent], None]
CancellationCheck = Callable[[], bool]
JobProcessRunner = Callable[..., CommandResult]

_MAX_ENVIRONMENT_SIZE_BYTES = 20 * 1024 * 1024 * 1024
_MAX_ENVIRONMENT_MEMBERS = 1_000_000
_BUILD_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


class CondaExecutor:
    """Install and execute only conda-pack plugin builds."""

    def __init__(
        self,
        *,
        data_root: Path,
        command_runner: Callable[..., CommandResult] | None = None,
        event_callback: EventCallback | None = None,
        cancellation_requested: CancellationCheck | None = None,
        job_process_runner: JobProcessRunner | None = None,
        max_extracted_size_bytes: int = _MAX_ENVIRONMENT_SIZE_BYTES,
        max_archive_members: int = _MAX_ENVIRONMENT_MEMBERS,
    ) -> None:
        if max_extracted_size_bytes <= 0 or max_archive_members <= 0:
            raise ValueError("conda extraction limits must be positive")
        self._data_root = data_root.resolve()
        self._environments_root = self._data_root / "environments"
        self._command_runner = command_runner or _run_command
        self._event_callback = event_callback or (lambda event: None)
        self._cancellation_requested = cancellation_requested or (lambda: False)
        self._job_process_runner = job_process_runner or _run_job_process
        self._max_extracted_size_bytes = max_extracted_size_bytes
        self._max_archive_members = max_archive_members

    def install(self, build: RunnerBuild) -> InstallResult:
        if build.runtime_type != "conda-pack":
            raise ValueError("CondaExecutor only accepts conda-pack builds")
        if _BUILD_ID_PATTERN.fullmatch(build.id) is None:
            return InstallResult(status="FAILED", error_summary="invalid Build ID")
        final_root = self._environment_root(build.id)
        temporary_root = self._environments_root / f"{build.id}-{uuid.uuid4().hex}.tmp"
        archive_path = self._safe_data_path(build.runtime_archive)
        promoted = False
        try:
            self._environments_root.mkdir(parents=True, exist_ok=True)
            if final_root.exists():
                raise FileExistsError("immutable environment already exists")
            _extract_tar_zst(
                archive_path,
                temporary_root,
                max_extracted_size_bytes=self._max_extracted_size_bytes,
                max_archive_members=self._max_archive_members,
            )
            os.replace(temporary_root, final_root)
            promoted = True
            unpack = final_root / "bin" / "conda-unpack"
            python = final_root / "bin" / "python"
            unpack_result = self._run([str(unpack)], timeout=build.timeout_seconds)
            if unpack_result.returncode != 0:
                return _failed_install(
                    temporary_root,
                    final_root,
                    unpack_result.stderr or "conda-unpack failed",
                    exit_code=unpack_result.returncode,
                    remove_final=promoted,
                )
            healthcheck = self._run(
                [
                    str(python),
                    "-c",
                    "import hub_runner, python_hub_contracts, python_hub_sdk",
                ],
                timeout=build.timeout_seconds,
            )
            if healthcheck.returncode != 0:
                return _failed_install(
                    temporary_root,
                    final_root,
                    healthcheck.stderr or "conda healthcheck failed",
                    exit_code=healthcheck.returncode,
                    remove_final=promoted,
                )
        except Exception as error:
            return _failed_install(
                temporary_root,
                final_root,
                str(error),
                remove_final=promoted,
            )
        return InstallResult(
            status="SUCCESS",
            environment_path=f"environments/{build.id}",
            metadata={"healthcheck": "ok"},
            exit_code=healthcheck.returncode,
        )

    def execute(self, job: RunnerJob) -> RunnerCompletion:
        if job.runtime_type != "conda-pack":
            raise ValueError("CondaExecutor only accepts conda-pack jobs")
        if job.cancel_requested:
            return RunnerCompletion(status=JobStatus.CANCELLED, exit_code=None)
        if job.environment_path is None:
            raise ValueError("conda-pack jobs require environment_path")
        environment_root = self._safe_data_path(job.environment_path)
        workspace_root = self._safe_data_path(job.workspace)
        command = [
            str(environment_root / "bin" / "python"),
            "-m",
            "hub_runner",
            "--job",
            str(self._safe_data_path(job.job_path)),
            "--manifest",
            str(self._safe_data_path(job.manifest_path)),
            "--plugin-root",
            str(self._safe_data_path(job.plugin_root)),
            "--result",
            str(self._safe_data_path(job.result_path)),
        ]
        env = {
            "HUB_INPUT_DIR": str(workspace_root / "input"),
            "HUB_WORK_DIR": str(workspace_root / "work"),
            "HUB_OUTPUT_DIR": str(workspace_root / "output"),
            "HUB_LOG_DIR": str(workspace_root / "logs"),
            "PYTHONUNBUFFERED": "1",
        }
        try:
            result = self._job_process_runner(
                command,
                cwd=workspace_root,
                env=env,
                timeout=job.timeout_seconds,
                event_callback=self._event_callback,
                cancellation_requested=self._cancellation_requested,
            )
        except subprocess.TimeoutExpired:
            return RunnerCompletion(
                status=JobStatus.TIMED_OUT,
                exit_code=None,
                error_summary="JOB_TIMED_OUT",
            )
        if result.termination_reason == "cancelled":
            return RunnerCompletion(
                status=JobStatus.CANCELLED,
                exit_code=result.returncode,
            )
        if result.termination_reason == "timeout":
            return RunnerCompletion(
                status=JobStatus.TIMED_OUT,
                exit_code=result.returncode,
                error_summary="JOB_TIMED_OUT",
            )
        if not result.events_forwarded:
            for line in result.stdout.splitlines():
                event = parse_runner_line(line)
                if event is not None:
                    self._event_callback(event)
        if result.returncode == 0:
            return RunnerCompletion(status=JobStatus.SUCCESS, exit_code=0)
        return RunnerCompletion(
            status=JobStatus.FAILED,
            exit_code=result.returncode,
            error_summary=(result.stderr or "conda runner failed")[:2000],
        )

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        return self._command_runner(args, cwd=cwd, env=env, timeout=timeout)

    def _safe_data_path(self, relative_path: str) -> Path:
        candidate = (self._data_root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self._data_root)
        except ValueError as error:
            raise ValueError("runner path must stay inside data root") from error
        return candidate

    def _environment_root(self, build_id: str) -> Path:
        return self._safe_data_path(f"environments/{build_id}")


def _run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CommandResult:
    completed = subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
        start_new_session=(os.name != "nt"),
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _run_job_process(
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    event_callback: EventCallback,
    cancellation_requested: CancellationCheck,
) -> CommandResult:
    """Run one plugin in a new process group while streaming events and polling cancel."""
    process = subprocess.Popen(
        list(args),
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=(os.name != "nt"),
        creationflags=(
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        ),
    )
    if process.stdout is None or process.stderr is None:
        _terminate_process_group(process)
        raise RuntimeError("runner process pipes were not created")

    messages: queue.Queue[tuple[Literal["stdout", "stderr"], str | None]] = (
        queue.Queue()
    )
    readers = [
        threading.Thread(
            target=_read_process_stream,
            args=("stdout", process.stdout, messages),
            daemon=True,
        ),
        threading.Thread(
            target=_read_process_stream,
            args=("stderr", process.stderr, messages),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    stdout: list[str] = []
    stderr: list[str] = []
    open_streams = len(readers)
    deadline = time.monotonic() + timeout
    termination_reason: Literal["cancelled", "timeout"] | None = None
    control_error: str | None = None
    try:
        while process.poll() is None or open_streams:
            if process.poll() is None and termination_reason is None:
                try:
                    cancel_now = cancellation_requested()
                except Exception as error:
                    cancel_now = False
                    control_error = f"cancellation check failed: {error}"
                    _terminate_process_group(process)
                if cancel_now:
                    termination_reason = "cancelled"
                    _terminate_process_group(process)
                elif time.monotonic() >= deadline:
                    termination_reason = "timeout"
                    _terminate_process_group(process)
            try:
                source, line = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            if line is None:
                open_streams -= 1
                continue
            if source == "stderr":
                stderr.append(line)
                continue
            stdout.append(line)
            try:
                event = parse_runner_line(line.rstrip("\r\n"))
                if event is not None:
                    event_callback(event)
            except Exception as error:
                control_error = f"event forwarding failed: {error}"
                _terminate_process_group(process)

        for reader in readers:
            reader.join(timeout=1)
        returncode = process.wait()
        if control_error is not None:
            stderr.append(control_error)
            if returncode == 0:
                returncode = 1
        return CommandResult(
            returncode=returncode,
            stdout="".join(stdout),
            stderr="".join(stderr),
            termination_reason=termination_reason,
            events_forwarded=True,
        )
    finally:
        if process.poll() is None:
            _terminate_process_group(process)


def _read_process_stream(
    source: Literal["stdout", "stderr"],
    stream: IO[str],
    messages: queue.Queue[tuple[Literal["stdout", "stderr"], str | None]],
) -> None:
    try:
        for line in stream:
            messages.put((source, line))
    finally:
        stream.close()
        messages.put((source, None))


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            kill_process_group = os.__dict__["killpg"]
            kill_process_group(process.pid, signal.SIGTERM)
    except (OSError, ValueError):
        process.terminate()
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            kill_process_group = os.__dict__["killpg"]
            kill_process_group(process.pid, signal.__dict__["SIGKILL"])
    except (OSError, ValueError):
        process.kill()
    process.wait()


def _failed_install(
    temporary_root: Path,
    final_root: Path,
    error_summary: str,
    *,
    exit_code: int | None = None,
    remove_final: bool = False,
) -> InstallResult:
    shutil.rmtree(temporary_root, ignore_errors=True)
    if remove_final:
        shutil.rmtree(final_root, ignore_errors=True)
    return InstallResult(
        status="FAILED",
        error_summary=error_summary[:2000],
        exit_code=exit_code,
    )


def _extract_tar_zst(
    archive_path: Path,
    destination: Path,
    *,
    max_extracted_size_bytes: int,
    max_archive_members: int,
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with (
        archive_path.open("rb") as raw,
        zstandard.ZstdDecompressor().stream_reader(raw) as reader,
        tarfile.open(fileobj=reader, mode="r|") as archive,
    ):
        _extract_members(
            archive,
            destination,
            max_extracted_size_bytes=max_extracted_size_bytes,
            max_archive_members=max_archive_members,
        )


def _extract_members(
    archive: tarfile.TarFile,
    destination: Path,
    *,
    max_extracted_size_bytes: int,
    max_archive_members: int,
) -> None:
    seen: set[str] = set()
    total_size = 0
    for member in archive:
        if len(seen) >= max_archive_members:
            raise ValueError("environment archive member limit exceeded")
        safe_name = _safe_member_name(member.name)
        if safe_name in seen:
            raise ValueError("duplicate archive member")
        seen.add(safe_name)
        if member.size < 0 or member.size > max_extracted_size_bytes:
            raise ValueError("environment archive size limit exceeded")
        total_size += member.size
        if total_size > max_extracted_size_bytes:
            raise ValueError("environment archive size limit exceeded")
        target = (destination / safe_name).resolve(strict=False)
        _require_contained(target, destination)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o777)
        elif member.issym() or member.islnk():
            _validate_link_target(member, target, destination)
            archive.extract(member, destination, filter="fully_trusted")
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("missing archive file payload")
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
        else:
            raise ValueError("unsupported archive member type")


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if path.is_absolute() or path.drive or not normalized or "\x00" in normalized:
        raise ValueError("unsafe archive member path")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError("unsafe archive member path")
    return normalized


def _validate_link_target(
    member: tarfile.TarInfo,
    target: Path,
    destination: Path,
) -> None:
    link_target = Path(member.linkname.replace("\\", "/"))
    if link_target.is_absolute() or link_target.drive:
        raise ValueError("unsafe archive link target")
    link_base = target.parent if member.issym() else destination
    resolved = (link_base / link_target).resolve(strict=False)
    _require_contained(resolved, destination)


def _require_contained(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise ValueError("archive member escapes destination") from error
