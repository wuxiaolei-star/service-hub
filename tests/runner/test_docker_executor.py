from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest
import zstandard
from hub_runner.docker_executor import DockerExecutor, RunnerBuild, RunnerJob
from python_hub_contracts import JobStatus, ProgressEvent

from deploy.runner.docker_runner_service import _job_from_payload, _reconcile_interrupted_jobs


class FakeImage:
    def __init__(self, image_id: str) -> None:
        self.id = image_id


class FakeImages:
    def __init__(self, image_id: str) -> None:
        self.image_id = image_id
        self.loaded_bytes = b""

    def load(self, data: Any) -> list[FakeImage]:
        self.loaded_bytes = data.read()
        return [FakeImage(self.image_id)]


class FakeContainer:
    def __init__(self, *, status_code: int = 0, logs: list[bytes] | None = None) -> None:
        self.status_code = status_code
        self._logs = logs or []
        self.removed = False
        self.stopped = False
        self.killed = False

    def logs(self, **_: Any) -> Iterable[bytes]:
        return iter(self._logs)

    def wait(self, **_: Any) -> dict[str, int]:
        return {"StatusCode": self.status_code}

    def stop(self, **_: Any) -> None:
        self.stopped = True

    def kill(self) -> None:
        self.killed = True

    def remove(self, **_: Any) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.run_kwargs: dict[str, Any] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        self.run_kwargs = kwargs
        return self.container


class FakeDockerClient:
    def __init__(self, *, digest: str, container: FakeContainer | None = None) -> None:
        self.images = FakeImages(f"sha256:{digest}")
        self.containers = FakeContainers(container or FakeContainer())


def test_docker_executor_loads_archive_and_reports_verified_digest(tmp_path: Path) -> None:
    digest = "1" * 64
    archive = _write_zst(tmp_path / "image.tar.zst", b"docker image tar")
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="docker",
        runtime_archive=_relative_to_data(tmp_path, archive),
        image_digest=digest,
        timeout_seconds=60,
    )
    client = FakeDockerClient(digest=digest)

    result = DockerExecutor(client=client, data_root=tmp_path).install(build)

    assert result.status == "SUCCESS"
    assert result.image_digest == digest
    assert client.images.loaded_bytes == b"docker image tar"


def test_docker_executor_rejects_conda_build(tmp_path: Path) -> None:
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive="plugins/plugin_build_123/runtime/env.tar.zst",
        image_digest=None,
    )

    with pytest.raises(ValueError, match="docker"):
        DockerExecutor(client=FakeDockerClient(digest="1" * 64), data_root=tmp_path).install(build)


def test_docker_executor_rejects_mismatched_loaded_digest(tmp_path: Path) -> None:
    archive = _write_zst(tmp_path / "image.tar.zst", b"docker image tar")
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="docker",
        runtime_archive=_relative_to_data(tmp_path, archive),
        image_digest="1" * 64,
    )

    result = DockerExecutor(
        client=FakeDockerClient(digest="2" * 64),
        data_root=tmp_path,
    ).install(build)

    assert result.status == "FAILED"
    assert result.error_summary == "Docker image digest mismatch"


def test_docker_executor_runs_digest_pinned_container_with_only_job_mounts(
    tmp_path: Path,
) -> None:
    container = FakeContainer(
        logs=[b'@@HUB@@{"protocol_version":"1.0","type":"progress","percent":40}\n']
    )
    client = FakeDockerClient(digest="1" * 64, container=container)
    events: list[ProgressEvent] = []
    job = _docker_job()

    result = DockerExecutor(
        client=client,
        data_root=tmp_path,
        event_callback=events.append,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    assert result.exit_code == 0
    assert client.containers.run_kwargs["image"] == f"sha256:{job.image_digest}"
    assert client.containers.run_kwargs["network_mode"] == "none"
    assert client.containers.run_kwargs["user"] == "65532:65532"
    assert client.containers.run_kwargs["read_only"] is True
    assert client.containers.run_kwargs["cap_drop"] == ["ALL"]
    assert client.containers.run_kwargs["command"][-2:] == [
        "--result",
        "/job/output/result.json",
    ]
    volumes = client.containers.run_kwargs["volumes"]
    assert sorted((value["bind"], value["mode"]) for value in volumes.values()) == [
        ("/job/input", "ro"),
        ("/job/job.json", "ro"),
        ("/job/logs", "rw"),
        ("/job/output", "rw"),
        ("/job/work", "rw"),
    ]
    assert all("/var/run/docker.sock" not in path for path in volumes)
    assert container.removed is True
    assert events == [
        ProgressEvent(protocol_version="1.0", type="progress", percent=40)
    ]


def test_docker_executor_rejects_cancelled_job_before_start(tmp_path: Path) -> None:
    client = FakeDockerClient(digest="1" * 64)
    job = _docker_job(cancel_requested=True)

    result = DockerExecutor(client=client, data_root=tmp_path).execute(job)

    assert result.status is JobStatus.CANCELLED
    assert client.containers.run_kwargs == {}


def test_compose_adds_docker_runner_as_only_socket_mount() -> None:
    compose = Path("compose.yaml").read_text("utf-8")

    assert "hub-docker-runner:" in compose
    hub_section = compose.split("  hub:", maxsplit=1)[1].split("  hub-conda-runner:", maxsplit=1)[0]
    conda_section = compose.split("  hub-conda-runner:", maxsplit=1)[1].split(
        "  hub-docker-runner:", maxsplit=1
    )[0]
    docker_section = compose.split("  hub-docker-runner:", maxsplit=1)[1]
    assert "/var/run/docker.sock" not in hub_section
    assert "/var/run/docker.sock" not in conda_section
    assert "/var/run/docker.sock:/var/run/docker.sock" in docker_section
    assert "ports:" not in docker_section


def test_docker_runner_service_maps_claim_to_digest_runtime_job() -> None:
    job = _job_from_payload(
        {
            "job_id": "job_123",
            "runtime_type": "docker",
            "cancel_requested": False,
            "workspace": "jobs/job_123",
            "paths": {"job": "job.json", "result": "result.json"},
            "job": {"execution": {"timeout": 30}},
            "build": {
                "runtime": {
                    "type": "docker",
                    "archive": "image.tar.zst",
                    "image": "x",
                    "digest": "1" * 64,
                },
                "image_digest": "1" * 64,
            },
        }
    )

    assert job.runtime_type == "docker"
    assert job.job_path == "jobs/job_123/job.json"
    assert job.result_path == "jobs/job_123/output/result.json"
    assert job.image_digest == "1" * 64


def test_docker_runner_service_reconciles_interrupted_jobs_before_polling() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post(_: str, __: str, path: str, payload: dict[str, object]) -> None:
        calls.append((path, payload))

    _reconcile_interrupted_jobs("http://hub/internal/v1", "token", post=post)

    assert calls == [
        ("/jobs/reconcile", {"runtime_type": "docker"}),
    ]


def _docker_job(*, cancel_requested: bool = False) -> RunnerJob:
    return RunnerJob(
        id="job_123",
        runtime_type="docker",
        workspace="jobs/job_123",
        job_path="jobs/job_123/job.json",
        result_path="jobs/job_123/output/result.json",
        image_digest="1" * 64,
        timeout_seconds=30,
        cancel_requested=cancel_requested,
    )


def _write_zst(path: Path, payload: bytes) -> Path:
    path.write_bytes(zstandard.ZstdCompressor().compress(payload))
    return path


def _relative_to_data(data_root: Path, path: Path) -> str:
    return path.resolve().relative_to(data_root.resolve()).as_posix()
