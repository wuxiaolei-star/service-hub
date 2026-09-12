from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
import zstandard
from hub_runner.docker_executor import (
    SERVICE_HUB_JOB_LABEL,
    SERVICE_HUB_OWNER_LABEL,
    DockerExecutor,
    RunnerBuild,
    RunnerJob,
    cleanup_owned_plugin_containers,
    service_hub_owner_value,
)
from python_hub_contracts import JobStatus, ProgressEvent

from deploy.runner import docker_runner_service
from deploy.runner.docker_runner_service import (
    _cancellation_checker,
    _job_from_payload,
    _reconcile_interrupted_jobs,
)


class ReadTimeout(Exception):
    pass


ReadTimeout.__module__ = "requests.exceptions"


class ReadTimeoutError(Exception):
    pass


ReadTimeoutError.__module__ = "urllib3.exceptions"


class DockerAPIWaitTimeout(Exception):
    def __init__(self, status_code: int) -> None:
        self.response = type("Response", (), {"status_code": status_code})()


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
    def __init__(
        self,
        *,
        status_code: int = 0,
        logs: list[bytes] | None = None,
        wait_results: list[dict[str, int] | BaseException] | None = None,
        stop_error: Exception | None = None,
        kill_error: Exception | None = None,
        remove_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._logs = logs or []
        self._wait_results = wait_results or []
        self.wait_timeouts: list[float] = []
        self.removed = False
        self.stopped = False
        self.killed = False
        self.stop_error = stop_error
        self.kill_error = kill_error
        self.remove_error = remove_error

    def logs(self, **kwargs: Any) -> bytes | Iterable[bytes]:
        return iter(self._logs) if kwargs.get("stream") else b"".join(self._logs)

    def wait(self, **kwargs: Any) -> dict[str, int]:
        self.wait_timeouts.append(float(kwargs["timeout"]))
        if self._wait_results:
            result = self._wait_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return {"StatusCode": self.status_code}

    def stop(self, **_: Any) -> None:
        self.stopped = True
        if self.stop_error:
            raise self.stop_error

    def kill(self) -> None:
        self.killed = True
        if self.kill_error:
            raise self.kill_error

    def remove(self, **_: Any) -> None:
        if self.remove_error:
            raise self.remove_error
        self.removed = True


class FakeContainers:
    def __init__(
        self,
        container: FakeContainer,
        *,
        listed: list[FakeContainer] | None = None,
    ) -> None:
        self.container = container
        self.listed = listed or []
        self.run_kwargs: dict[str, Any] = {}
        self.list_kwargs: dict[str, Any] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        self.run_kwargs = kwargs
        return self.container

    def list(self, **kwargs: Any) -> list[FakeContainer]:
        self.list_kwargs = kwargs
        return self.listed


class FakeDockerClient:
    def __init__(
        self,
        *,
        digest: str,
        container: FakeContainer | None = None,
        listed: list[FakeContainer] | None = None,
    ) -> None:
        self.images = FakeImages(f"sha256:{digest}")
        self.containers = FakeContainers(container or FakeContainer(), listed=listed)


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

    result = DockerExecutor(
        client=client,
        data_root=tmp_path,
        docker_host_data_root="/tmp/hub-data",
    ).install(build)

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
        DockerExecutor(
            client=FakeDockerClient(digest="1" * 64),
            data_root=tmp_path,
            docker_host_data_root="/tmp/hub-data",
        ).install(build)


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
        docker_host_data_root="/tmp/hub-data",
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
        docker_host_data_root="/srv/python-service-hub/data",
        event_callback=events.append,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    assert result.exit_code == 0
    assert client.containers.run_kwargs["image"] == f"sha256:{job.image_digest}"
    assert client.containers.run_kwargs["network_mode"] == "none"
    assert client.containers.run_kwargs["user"] == "65532:65532"
    assert client.containers.run_kwargs["read_only"] is True
    assert client.containers.run_kwargs["cap_drop"] == ["ALL"]
    assert client.containers.run_kwargs["labels"] == {
        SERVICE_HUB_OWNER_LABEL: service_hub_owner_value(
            "/srv/python-service-hub/data"
        ),
        SERVICE_HUB_JOB_LABEL: "job_123",
    }
    assert client.containers.run_kwargs["command"][-2:] == [
        "--result",
        "/job/output/result.json",
    ]
    volumes = client.containers.run_kwargs["volumes"]
    assert set(volumes) == {
        "/srv/python-service-hub/data/jobs/job_123/input",
        "/srv/python-service-hub/data/jobs/job_123/job.json",
        "/srv/python-service-hub/data/jobs/job_123/work",
        "/srv/python-service-hub/data/jobs/job_123/output",
        "/srv/python-service-hub/data/jobs/job_123/logs",
    }
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


def test_cleanup_stops_and_removes_only_containers_owned_by_this_hub() -> None:
    first = FakeContainer()
    second = FakeContainer()
    client = FakeDockerClient(digest="1" * 64, listed=[first, second])

    removed = cleanup_owned_plugin_containers(
        client,
        docker_host_data_root="/srv/python-service-hub/data",
    )

    assert client.containers.list_kwargs == {
        "all": True,
        "filters": {
            "label": [
                f"{SERVICE_HUB_OWNER_LABEL}="
                f"{service_hub_owner_value('/srv/python-service-hub/data')}"
            ]
        },
    }
    assert removed == 2
    assert all(container.stopped for container in (first, second))
    assert all(container.removed for container in (first, second))


@pytest.mark.parametrize(
    "host_root",
    [
        "/srv/python-service-hub/data",
        "/srv/python-service-hub/data/",
        "/srv//python-service-hub/./data///",
    ],
)
def test_owner_label_command_matches_runner_for_equivalent_paths(
    tmp_path: Path, host_root: str
) -> None:
    # Exercise the exact Python command used by the backup runbook in the image.
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    command = subprocess.run(
        [sys.executable, "-m", "hub_runner.docker_executor", host_root],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    client = FakeDockerClient(digest="1" * 64)
    DockerExecutor(
        client=client,
        data_root=tmp_path,
        docker_host_data_root=host_root,
    ).execute(_docker_job())

    label = client.containers.run_kwargs["labels"][SERVICE_HUB_OWNER_LABEL]
    assert command.stdout.strip() == label
    assert label == service_hub_owner_value("/srv/python-service-hub/data")
    assert label != service_hub_owner_value("/srv/another-hub/data")


def test_cleanup_continues_after_individual_stop_kill_and_remove_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = FakeContainer(
        stop_error=RuntimeError("stop unavailable"),
        kill_error=RuntimeError("kill unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    second = FakeContainer()
    client = FakeDockerClient(digest="1" * 64, listed=[first, second])

    removed = cleanup_owned_plugin_containers(
        client, docker_host_data_root="/srv/python-service-hub/data"
    )

    assert removed == 1
    assert first.stopped and not first.removed
    assert second.stopped and second.removed
    assert "remove unavailable" in caplog.text


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_error"),
    [
        ({"StatusCode": 0}, JobStatus.SUCCESS, None),
        ({"StatusCode": 7}, JobStatus.FAILED, "Docker container exited with status 7"),
        (RuntimeError("primary wait failure"), JobStatus.FAILED, "primary wait failure"),
        ("cancel", JobStatus.CANCELLED, None),
        ("timeout", JobStatus.TIMED_OUT, "JOB_TIMED_OUT"),
    ],
)
def test_job_result_survives_container_cleanup_failures(
    tmp_path: Path,
    outcome: Any,
    expected_status: JobStatus,
    expected_error: str | None,
) -> None:
    container = FakeContainer(
        wait_results=[outcome] if not isinstance(outcome, str) else [],
        stop_error=RuntimeError("stop unavailable"),
        kill_error=RuntimeError("kill unavailable"),
        remove_error=RuntimeError("remove unavailable"),
    )
    clock = iter([0.0, 30.0] if outcome == "timeout" else [0.0, 0.0])
    result = DockerExecutor(
        client=FakeDockerClient(digest="1" * 64, container=container),
        data_root=tmp_path,
        docker_host_data_root="/srv/python-service-hub/data",
        cancellation_requested=lambda: outcome == "cancel",
        monotonic=lambda: next(clock),
    ).execute(_docker_job())

    assert result.status == expected_status
    assert result.error_summary == expected_error


def test_runner_posts_job_completion_despite_container_remove_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = FakeContainer(remove_error=RuntimeError("remove unavailable"))
    client = FakeDockerClient(digest="1" * 64, container=container)
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    monkeypatch.setenv("HUB_INTERNAL_BASE_URL", "http://hub/internal/v1")
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "test-token")
    monkeypatch.setenv("HUB_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HUB_DOCKER_HOST_DATA_ROOT", "/srv/python-service-hub/data")
    monkeypatch.setattr(docker_runner_service.signal, "signal", lambda *_: None)
    monkeypatch.setattr(docker_runner_service, "_reconcile_interrupted_jobs", lambda *_, **__: None)
    completions: list[dict[str, Any]] = []

    def post(_: str, __: str, path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if path == "/operations/claim":
            return None
        if path == "/jobs/claim":
            return {
                "job_id": "job_123",
                "workspace": "jobs/job_123",
                "paths": {"job": "job.json", "result": "result.json"},
                "build": {"image_digest": "1" * 64},
                "job": {"job": {"id": "job_123"}, "execution": {"timeout": 30}},
                "cancel_requested": False,
            }
        if path == "/jobs/job_123/complete":
            completions.append(payload)
            raise SystemExit(0)
        raise AssertionError(f"Unexpected request: {path}")

    monkeypatch.setattr(docker_runner_service, "_post", post)
    monkeypatch.setattr(docker_runner_service, "_cancellation_checker", lambda *_: lambda: False)
    with pytest.raises(SystemExit):
        docker_runner_service.main()

    assert len(completions) == 1
    assert completions[0]["result"]["status"] == "SUCCESS"
    assert completions[0]["exit_code"] == 0


def test_docker_runner_retries_startup_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Docker runner must wait for Hub startup before it begins normal polling."""
    client = FakeDockerClient(digest="1" * 64)
    reconciliations: list[None] = []
    startup_actions: list[Callable[[], None]] = []
    monkeypatch.setitem(sys.modules, "docker", SimpleNamespace(from_env=lambda: client))
    monkeypatch.setenv("HUB_INTERNAL_BASE_URL", "http://hub/internal/v1")
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "test-token")
    monkeypatch.setenv("HUB_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("HUB_DOCKER_HOST_DATA_ROOT", "/srv/python-service-hub/data")
    monkeypatch.setattr(docker_runner_service.signal, "signal", lambda *_: None)
    monkeypatch.setattr(
        docker_runner_service,
        "_reconcile_interrupted_jobs",
        lambda *_, **__: reconciliations.append(None),
    )

    def retry(action: Callable[[], None]) -> None:
        startup_actions.append(action)
        action()

    monkeypatch.setattr(docker_runner_service, "retry_startup", retry, raising=False)
    monkeypatch.setattr(
        docker_runner_service,
        "_post",
        lambda *_: (_ for _ in ()).throw(SystemExit("stop polling")),
    )

    with pytest.raises(SystemExit, match="stop polling"):
        docker_runner_service.main()

    assert len(startup_actions) == 1
    assert reconciliations == [None]


def test_docker_executor_rejects_cancelled_job_before_start(tmp_path: Path) -> None:
    client = FakeDockerClient(digest="1" * 64)
    job = _docker_job(cancel_requested=True)

    result = DockerExecutor(
        client=client,
        data_root=tmp_path,
        docker_host_data_root="/tmp/hub-data",
    ).execute(job)

    assert result.status is JobStatus.CANCELLED
    assert client.containers.run_kwargs == {}


def test_docker_executor_stops_running_container_when_cancellation_is_requested(
    tmp_path: Path,
) -> None:
    container = FakeContainer(wait_results=[ReadTimeout("poll elapsed")])
    client = FakeDockerClient(digest="1" * 64, container=container)
    cancellation_states = iter([False, True])

    result = DockerExecutor(
        client=client,
        data_root=tmp_path,
        docker_host_data_root="/tmp/hub-data",
        cancellation_requested=lambda: next(cancellation_states),
        monotonic=lambda: 0.0,
    ).execute(_docker_job())

    assert result.status is JobStatus.CANCELLED
    assert result.exit_code is None
    assert container.wait_timeouts == [1.0]
    assert container.stopped is True
    assert container.killed is False
    assert container.removed is True


def test_docker_executor_stops_owned_container_during_runner_shutdown(
    tmp_path: Path,
) -> None:
    container = FakeContainer(wait_results=[SystemExit(0)])
    client = FakeDockerClient(digest="1" * 64, container=container)

    with pytest.raises(SystemExit):
        DockerExecutor(
            client=client,
            data_root=tmp_path,
            docker_host_data_root="/tmp/hub-data",
            monotonic=lambda: 0.0,
        ).execute(_docker_job())

    assert container.stopped is True
    assert container.removed is True


@pytest.mark.parametrize(
    "wait_error",
    [
        TimeoutError("poll elapsed"),
        ReadTimeout("poll elapsed"),
        ReadTimeoutError("poll elapsed"),
        DockerAPIWaitTimeout(408),
        DockerAPIWaitTimeout(504),
    ],
)
def test_docker_executor_maps_wait_timeouts_to_job_timed_out(
    tmp_path: Path, wait_error: Exception
) -> None:
    container = FakeContainer(wait_results=[wait_error])
    client = FakeDockerClient(digest="1" * 64, container=container)
    clock_values = iter([0.0, 0.0, 30.0])

    result = DockerExecutor(
        client=client,
        data_root=tmp_path,
        docker_host_data_root="/tmp/hub-data",
        cancellation_requested=lambda: False,
        monotonic=lambda: next(clock_values),
    ).execute(_docker_job())

    assert result.status is JobStatus.TIMED_OUT
    assert result.exit_code is None
    assert result.error_summary == "JOB_TIMED_OUT"
    assert container.wait_timeouts == [1.0]
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.parametrize(
    "host_root",
    ["data", "../data", "/", "/srv/../data", "//host/data"],
)
def test_docker_executor_rejects_unsafe_host_data_root(
    tmp_path: Path, host_root: str
) -> None:
    with pytest.raises(ValueError, match="absolute trusted directory"):
        DockerExecutor(
            client=FakeDockerClient(digest="1" * 64),
            data_root=tmp_path,
            docker_host_data_root=host_root,
        )


def test_unified_service_starts_the_docker_runner_with_one_socket_mount() -> None:
    """The shared Hub image must supervise the retained Docker Runner entrypoint."""
    model = yaml.safe_load(Path("compose.yaml").read_text("utf-8"))
    supervisor = Path("deploy/service_hub/supervisord.conf").read_text("utf-8")

    service = model["services"]["service-hub"]
    assert set(model["services"]) == {"service-hub"}
    assert service["image"] == "python-service-hub:1.0.0-linux-amd64"
    assert service["volumes"].count("/var/run/docker.sock:/var/run/docker.sock") == 1
    assert "[program:docker-runner]" in supervisor
    assert "python -m deploy.runner.docker_runner_service" in supervisor


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
    orphan = FakeContainer()
    client = FakeDockerClient(digest="1" * 64, listed=[orphan])

    def post(_: str, __: str, path: str, payload: dict[str, object]) -> None:
        calls.append((path, payload))

    _reconcile_interrupted_jobs(
        "http://hub/internal/v1",
        "token",
        client=client,
        docker_host_data_root="/srv/python-service-hub/data",
        post=post,
    )

    assert calls == [
        ("/jobs/reconcile", {"runtime_type": "docker"}),
    ]
    assert orphan.stopped is True
    assert orphan.removed is True


def test_docker_runner_checks_authenticated_internal_cancellation_endpoint() -> None:
    calls: list[tuple[str, str, str, dict[str, object]]] = []

    def post(
        base_url: str,
        token: str,
        path: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        calls.append((base_url, token, path, payload))
        return {"cancel_requested": True}

    check = _cancellation_checker(
        "http://hub/internal/v1",
        "secret-token",
        "job_123",
        post=post,
    )

    assert check() is True
    assert calls == [
        (
            "http://hub/internal/v1",
            "secret-token",
            "/jobs/job_123/cancellation",
            {"runtime_type": "docker"},
        )
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
