from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol, cast

import zstandard
from python_hub_contracts import JobStatus, RunnerEvent, RuntimeType, parse_runner_line


class ImageProtocol(Protocol):
    id: str


class ImagesProtocol(Protocol):
    def load(self, data: Any) -> list[ImageProtocol]: ...


class ContainerProtocol(Protocol):
    def logs(self, **kwargs: Any) -> bytes | str | Iterable[bytes | str]: ...

    def wait(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def stop(self, **kwargs: Any) -> None: ...

    def kill(self) -> None: ...

    def remove(self, **kwargs: Any) -> None: ...


class ContainersProtocol(Protocol):
    def run(self, **kwargs: Any) -> ContainerProtocol: ...

    def list(self, **kwargs: Any) -> list[ContainerProtocol]: ...


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
CancellationCheck = Callable[[], bool]
MonotonicClock = Callable[[], float]
_WAIT_POLL_SECONDS = 1.0
SERVICE_HUB_OWNER_LABEL = "io.python-service-hub.owner"
SERVICE_HUB_JOB_LABEL = "io.python-service-hub.job-id"
_LOGGER = logging.getLogger(__name__)


class _CancellationRequested(Exception):
    pass


class _JobTimedOut(Exception):
    pass


class DockerExecutor:
    """Install and execute only Docker plugin builds."""

    def __init__(
        self,
        *,
        client: DockerClientProtocol,
        data_root: Path,
        docker_host_data_root: str,
        event_callback: EventCallback | None = None,
        cancellation_requested: CancellationCheck | None = None,
        monotonic: MonotonicClock | None = None,
    ) -> None:
        self._client = client
        self._data_root = data_root.resolve()
        self._docker_host_data_root = _trusted_host_data_root(docker_host_data_root)
        self._event_callback = event_callback or (lambda event: None)
        self._cancellation_requested = cancellation_requested or (lambda: False)
        self._monotonic = monotonic or time.monotonic

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
        container_needs_stop = False
        deadline = self._monotonic() + job.timeout_seconds
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
                    "/job/output/result.json",
                ],
                detach=True,
                network_mode="none",
                user="65532:65532",
                read_only=True,
                cap_drop=["ALL"],
                labels={
                    SERVICE_HUB_OWNER_LABEL: service_hub_owner_value(
                        str(self._docker_host_data_root)
                    ),
                    SERVICE_HUB_JOB_LABEL: job.id,
                },
                working_dir="/",
                environment={
                    "HUB_INPUT_DIR": "/job/input",
                    "HUB_WORK_DIR": "/job/work",
                    "HUB_OUTPUT_DIR": "/job/output",
                    "HUB_LOG_DIR": "/job/logs",
                    "PYTHONUNBUFFERED": "1",
                },
                volumes={
                    self._docker_host_path(input_root): {"bind": "/job/input", "mode": "ro"},
                    self._docker_host_path(self._safe_data_path(job.job_path)): {
                        "bind": "/job/job.json",
                        "mode": "ro",
                    },
                    self._docker_host_path(work_root): {"bind": "/job/work", "mode": "rw"},
                    self._docker_host_path(output_root): {
                        "bind": "/job/output",
                        "mode": "rw",
                    },
                    self._docker_host_path(logs_root): {"bind": "/job/logs", "mode": "rw"},
                },
                stdout=True,
                stderr=True,
            )
            container_needs_stop = True
            result = self._wait_for_container(container, deadline)
            container_needs_stop = False
            self._forward_logs(container.logs(stdout=True, stderr=True))
        except _CancellationRequested:
            _stop_container(container)
            container_needs_stop = False
            return RunnerCompletion(status=JobStatus.CANCELLED, exit_code=None)
        except _JobTimedOut:
            _stop_container(container)
            container_needs_stop = False
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
                if container_needs_stop:
                    _stop_container(container)
                _remove_container(container)

        exit_code = int(result.get("StatusCode", 1))
        if exit_code == 0:
            return RunnerCompletion(status=JobStatus.SUCCESS, exit_code=0)
        return RunnerCompletion(
            status=JobStatus.FAILED,
            exit_code=exit_code,
            error_summary=f"Docker container exited with status {exit_code}",
        )

    def _wait_for_container(
        self, container: ContainerProtocol, deadline: float
    ) -> Mapping[str, Any]:
        while True:
            if self._cancellation_requested():
                raise _CancellationRequested
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise _JobTimedOut
            try:
                return container.wait(timeout=min(_WAIT_POLL_SECONDS, remaining))
            except Exception as error:
                if _is_docker_wait_timeout(error):
                    continue
                raise

    def _safe_data_path(self, relative_path: str) -> Path:
        candidate = (self._data_root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self._data_root)
        except ValueError as error:
            raise ValueError("runner path must stay inside data root") from error
        return candidate

    def _docker_host_path(self, container_path: Path) -> str:
        """Map a container-local /data path to the Docker daemon host bind source."""
        relative = container_path.resolve(strict=False).relative_to(self._data_root)
        return str(self._docker_host_data_root.joinpath(*relative.parts))

    def _forward_logs(self, lines: bytes | str | Iterable[bytes | str]) -> None:
        if isinstance(lines, bytes | str):
            lines = [lines]
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


def _trusted_host_data_root(value: str) -> PurePosixPath:
    root = PurePosixPath(value)
    if (
        not root.is_absolute()
        or root.anchor != "/"
        or root == PurePosixPath("/")
        or ".." in root.parts
    ):
        raise ValueError("docker_host_data_root must be an absolute trusted directory")
    return root


def service_hub_owner_value(docker_host_data_root: str) -> str:
    """Return the stable ownership label for one Hub data-root deployment."""
    root = _trusted_host_data_root(docker_host_data_root)
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return f"python-service-hub-{digest}"


def cleanup_owned_plugin_containers(
    client: DockerClientProtocol,
    *,
    docker_host_data_root: str,
) -> int:
    """Stop and remove only plugin containers labeled for this Hub deployment."""
    owner = service_hub_owner_value(docker_host_data_root)
    containers = client.containers.list(
        all=True,
        filters={"label": [f"{SERVICE_HUB_OWNER_LABEL}={owner}"]},
    )
    removed = 0
    for container in containers:
        _stop_container(container)
        removed += _remove_container(container)
    return removed


def _is_docker_wait_timeout(error: Exception) -> bool:
    """Recognize Python, requests, urllib3, and timeout HTTP response variants."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, subprocess.TimeoutExpired)):
            return True
        response = getattr(current, "response", None)
        if getattr(response, "status_code", None) in {408, 504}:
            return True
        if any(
            exception_type.__module__ in {"requests.exceptions", "urllib3.exceptions"}
            and exception_type.__name__
            in {"Timeout", "ReadTimeout", "TimeoutError", "ReadTimeoutError"}
            for exception_type in type(current).__mro__
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _stop_container(container: ContainerProtocol | None) -> None:
    if container is None:
        return
    try:
        container.stop(timeout=10)
    except Exception:
        _LOGGER.warning("Could not stop plugin container; attempting kill", exc_info=True)
        try:
            container.kill()
        except Exception:
            _LOGGER.warning("Could not kill plugin container", exc_info=True)


def _remove_container(container: ContainerProtocol) -> bool:
    try:
        container.remove(force=True)
    except Exception:
        _LOGGER.warning("Could not remove plugin container", exc_info=True)
        return False
    return True


def load_result_payload(data_root: Path, result_path: str) -> dict[str, Any] | None:
    path = (data_root / result_path).resolve(strict=False)
    try:
        path.relative_to(data_root.resolve())
    except ValueError as error:
        raise ValueError("result path must stay inside data root") from error
    if not path.exists():
        return None
    return cast(dict[str, Any], json.loads(path.read_text("utf-8")))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m hub_runner.docker_executor HOST_DATA_ROOT")
    print(service_hub_owner_value(sys.argv[1]))
