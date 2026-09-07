from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import zstandard
from python_hub_contracts import JobStatus, RunnerEvent, RuntimeType, parse_runner_line


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


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


class CondaExecutor:
    """Install and execute only conda-pack plugin builds."""

    def __init__(
        self,
        *,
        data_root: Path,
        command_runner: Callable[..., CommandResult] | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self._data_root = data_root.resolve()
        self._environments_root = self._data_root / "environments"
        self._command_runner = command_runner or _run_command
        self._event_callback = event_callback or (lambda event: None)

    def install(self, build: RunnerBuild) -> InstallResult:
        if build.runtime_type != "conda-pack":
            raise ValueError("CondaExecutor only accepts conda-pack builds")
        final_root = self._environment_root(build.id)
        temporary_root = self._environments_root / f"{build.id}-{uuid.uuid4().hex}.tmp"
        archive_path = self._safe_data_path(build.runtime_archive)
        try:
            self._environments_root.mkdir(parents=True, exist_ok=True)
            _extract_tar_zst(archive_path, temporary_root)
            os.replace(temporary_root, final_root)
            unpack = final_root / "bin" / "conda-unpack"
            python = final_root / "bin" / "python"
            unpack_result = self._run([str(unpack)], timeout=build.timeout_seconds)
            if unpack_result.returncode != 0:
                return _failed_install(
                    temporary_root,
                    final_root,
                    unpack_result.stderr or "conda-unpack failed",
                    exit_code=unpack_result.returncode,
                )
            healthcheck = self._run(
                [
                    str(python),
                    "-c",
                    "import h5py, scipy; from osgeo import ogr",
                ],
                timeout=build.timeout_seconds,
            )
            if healthcheck.returncode != 0:
                return _failed_install(
                    temporary_root,
                    final_root,
                    healthcheck.stderr or "conda healthcheck failed",
                    exit_code=healthcheck.returncode,
                )
        except Exception as error:
            return _failed_install(temporary_root, final_root, str(error))
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
            result = self._run(
                command,
                cwd=workspace_root,
                env=env,
                timeout=job.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return RunnerCompletion(
                status=JobStatus.TIMED_OUT,
                exit_code=None,
                error_summary="JOB_TIMED_OUT",
            )
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


def _failed_install(
    temporary_root: Path,
    final_root: Path,
    error_summary: str,
    *,
    exit_code: int | None = None,
) -> InstallResult:
    shutil.rmtree(temporary_root, ignore_errors=True)
    shutil.rmtree(final_root, ignore_errors=True)
    return InstallResult(
        status="FAILED",
        error_summary=error_summary[:2000],
        exit_code=exit_code,
    )


def _extract_tar_zst(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    with (
        archive_path.open("rb") as raw,
        zstandard.ZstdDecompressor().stream_reader(raw) as reader,
        tarfile.open(fileobj=reader, mode="r|") as archive,
    ):
        _extract_members(archive, destination)


def _extract_members(archive: tarfile.TarFile, destination: Path) -> None:
    seen: set[str] = set()
    for member in archive:
        safe_name = _safe_member_name(member.name)
        if safe_name in seen:
            raise ValueError("duplicate archive member")
        seen.add(safe_name)
        target = (destination / safe_name).resolve(strict=False)
        _require_contained(target, destination)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(member.mode & 0o777)
        elif member.issym() or member.islnk():
            _validate_link_target(member, target, destination)
            archive.extract(member, destination)
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
    resolved = (target.parent / link_target).resolve(strict=False)
    _require_contained(resolved, destination)


def _require_contained(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise ValueError("archive member escapes destination") from error
