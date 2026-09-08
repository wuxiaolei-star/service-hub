from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import zstandard
from python_hub_contracts import JobStatus, RunnerEvent, RuntimeType, parse_runner_line


class ImageProtocol(Protocol):
    id: str


class ImagesProtocol(Protocol):
    def load(self, data: Any) -> list[ImageProtocol]: ...


class ContainerProtocol(Protocol):
    def logs(self, **kwargs: Any) -> Iterable[bytes | str]: ...

    def wait(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def stop(self, **kwargs: Any) -> None: ...

    def kill(self) -> None: ...

    def remove(self, **kwargs: Any) -> None: ...


class ContainersProtocol(Protocol):
    def run(self, **kwargs: Any) -> ContainerProtocol: ...


class DockerClientProtocol(Protocol):
    images: ImagesProtocol
    containers: ContainersProtocol


@dataclass(frozen=True, slots=True)
class RunnerBuild:
    id: str
    runtime_type: RuntimeType
    runtime_archive: str
    image_digest: str | None
    timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class InstallResult:
    status: Literal["SUCCESS", "FAILED"]
    image_digest: str | None = None
    metadata: dict[str, object] | None = None
    error_summary: str | None = None
    exit_code: int | None = None


@dataclass(frozen=True, slots=True)
class RunnerJob:
    id: str
    runtime_type: RuntimeType
    workspace: str
    job_path: str
    result_path: str
    image_digest: str | None
    timeout_seconds: int
    cancel_requested: bool


@dataclass(frozen=True, slots=True)
class RunnerCompletion:
    status: JobStatus
    exit_code: int | None
    error_summary: str | None = None


EventCallback = Callable[[RunnerEvent], None]


class DockerExecutor:
    """Install and execute only Docker plugin builds."""

    def __init__(
        self,
        *,
        client: DockerClientProtocol,
        data_root: Path,
        event_callback: EventCallback | None = None,
    ) -> None:
        self._client = client
        self._data_root = data_root.resolve()
        self._event_callback = event_callback or (lambda event: None)

    def install(self, build: RunnerBuild) -> InstallResult:
        if build.runtime_type != "docker":
            raise ValueError("DockerExecutor only accepts docker builds")
        if build.image_digest is None:
            raise ValueError("docker builds require image_digest")
        archive_path = self._safe_data_path(build.runtime_archive)
        try:
            with (
                archive_path.open("rb") as raw,
                zstandard.ZstdDecompressor().stream_reader(raw) as reader,
            ):
                images = self._client.images.load(reader)
        except Exception as error:
            return InstallResult(status="FAILED", error_summary=str(error)[:2000])
        loaded_digest = _normalize_digest(images[0].id) if images else None
        if loaded_digest != build.image_digest:
            return InstallResult(
                status="FAILED",
                image_digest=loaded_digest,
                error_summary="Docker image digest mismatch",
            )
        return InstallResult(
            status="SUCCESS",
            image_digest=loaded_digest,
            metadata={"healthcheck": "ok"},
        )

    def execute(self, job: RunnerJob) -> RunnerCompletion:
        if job.runtime_type != "docker":
            raise ValueError("DockerExecutor only accepts docker jobs")
        if job.cancel_requested:
            return RunnerCompletion(status=JobStatus.CANCELLED, exit_code=None)
        if job.image_digest is None:
            raise ValueError("docker jobs require image_digest")

        workspace_root = self._safe_data_path(job.workspace)
        input_root = workspace_root / "input"
        work_root = workspace_root / "work"
        output_root = workspace_root / "output"
        logs_root = workspace_root / "logs"
        for writable_root in (work_root, output_root, logs_root):
            writable_root.mkdir(parents=True, exist_ok=True)
        image = f"sha256:{job.image_digest}"
        container: ContainerProtocol | None = None
        try:
            container = self._client.containers.run(
                image=image,
                command=[
                    "--job",
                    "/job/job.json",
                    "--manifest",
                    "/plugin/plugin.yaml",
                    "--plugin-root",
                    "/plugin",
                    "--result",
                    "/output/result.json",
                ],
                detach=True,
                network_mode="none",
                user="65532:65532",
                read_only=True,
                cap_drop=["ALL"],
                working_dir="/",
                environment={
                    "HUB_INPUT_DIR": "/job/input",
                    "HUB_WORK_DIR": "/job/work",
                    "HUB_OUTPUT_DIR": "/job/output",
                    "HUB_LOG_DIR": "/job/logs",
                    "PYTHONUNBUFFERED": "1",
                },
                volumes={
                    str(input_root): {"bind": "/job/input", "mode": "ro"},
                    str(self._safe_data_path(job.job_path)): {
                        "bind": "/job/job.json",
                        "mode": "ro",
                    },
                    str(work_root): {"bind": "/job/work", "mode": "rw"},
                    str(output_root): {"bind": "/job/output", "mode": "rw"},
                    str(logs_root): {"bind": "/job/logs", "mode": "rw"},
                },
                stdout=True,
                stderr=True,
            )
            result = container.wait(timeout=job.timeout_seconds)
            self._forward_logs(container.logs(stdout=True, stderr=True))
        except TimeoutError:
            _stop_container(container)
            return RunnerCompletion(
                status=JobStatus.TIMED_OUT,
                exit_code=None,
                error_summary="JOB_TIMED_OUT",
            )
        except subprocess.TimeoutExpired:
            _stop_container(container)
            return RunnerCompletion(
                status=JobStatus.TIMED_OUT,
                exit_code=None,
                error_summary="JOB_TIMED_OUT",
            )
        except Exception as error:
            return RunnerCompletion(
                status=JobStatus.FAILED,
                exit_code=None,
                error_summary=str(error)[:2000],
            )
        finally:
            if container is not None:
                container.remove(force=True)

        exit_code = int(result.get("StatusCode", 1))
        if exit_code == 0:
            return RunnerCompletion(status=JobStatus.SUCCESS, exit_code=0)
        return RunnerCompletion(
            status=JobStatus.FAILED,
            exit_code=exit_code,
            error_summary=f"Docker container exited with status {exit_code}",
        )

    def _safe_data_path(self, relative_path: str) -> Path:
        candidate = (self._data_root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self._data_root)
        except ValueError as error:
            raise ValueError("runner path must stay inside data root") from error
        return candidate

    def _forward_logs(self, lines: Iterable[bytes | str]) -> None:
        for raw_line in lines:
            text = (
                raw_line.decode("utf-8", errors="replace")
                if isinstance(raw_line, bytes)
                else raw_line
            )
            for line in text.splitlines():
                event = parse_runner_line(line)
                if event is not None:
                    self._event_callback(event)


def _normalize_digest(value: str | None) -> str | None:
    if value is None:
        return None
    prefix, separator, suffix = value.partition(":")
    if separator and prefix == "sha256":
        return suffix
    return value


def _stop_container(container: ContainerProtocol | None) -> None:
    if container is None:
        return
    try:
        container.stop(timeout=10)
    except Exception:
        container.kill()


def load_result_payload(data_root: Path, result_path: str) -> dict[str, Any] | None:
    path = (data_root / result_path).resolve(strict=False)
    try:
        path.relative_to(data_root.resolve())
    except ValueError as error:
        raise ValueError("result path must stay inside data root") from error
    if not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text("utf-8")))
