from __future__ import annotations

import io
import os
import stat
import tarfile
from collections.abc import Sequence
from pathlib import Path

import pytest
import zstandard
from hub_runner.conda_executor import CommandResult, CondaExecutor, RunnerBuild, RunnerJob
from python_hub_contracts import JobStatus, ProgressEvent

from deploy.runner.conda_runner_service import _job_from_payload, _reconcile_interrupted_jobs


class FakeCommandRunner:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.result = result or CommandResult(returncode=0, stdout="", stderr="")

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        del cwd, timeout
        self.calls.append([str(arg) for arg in args])
        self.environments.append(dict(env or {}))
        return self.result


def test_conda_executor_runs_conda_unpack_then_import_healthcheck(tmp_path: Path) -> None:
    """Skipping unpack or healthcheck would mark an unusable conda-pack environment ready."""
    archive = _write_env_archive(tmp_path / "env.tar.zst")
    fake_runner = FakeCommandRunner()
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
        timeout_seconds=60,
    )

    result = CondaExecutor(data_root=tmp_path, command_runner=fake_runner).install(build)

    env_root = tmp_path / "environments" / build.id
    assert result.status == "SUCCESS"
    assert result.environment_path == f"environments/{build.id}"
    assert fake_runner.calls == [
        [str(env_root / "bin" / "conda-unpack")],
        [
            str(env_root / "bin" / "python"),
            "-c",
            "import h5py, scipy; from osgeo import ogr",
        ],
    ]
    if os.name != "nt":
        assert (env_root / "bin" / "conda-unpack").stat().st_mode & stat.S_IXUSR
        assert (env_root / "bin" / "python").stat().st_mode & stat.S_IXUSR


def test_conda_executor_rejects_docker_build(tmp_path: Path) -> None:
    """Accepting docker builds in the conda runner breaks runtime isolation."""
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="docker",
        runtime_archive="plugins/plugin_build_123/image.tar.zst",
    )

    with pytest.raises(ValueError, match="conda-pack"):
        CondaExecutor(data_root=tmp_path).install(build)


def test_conda_executor_reports_failed_healthcheck_without_ready_environment(
    tmp_path: Path,
) -> None:
    """A nonzero unpack or healthcheck must not register the environment as READY."""
    archive = _write_env_archive(tmp_path / "env.tar.zst")
    fake_runner = FakeCommandRunner(CommandResult(returncode=2, stdout="", stderr="bad env"))
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
        timeout_seconds=60,
    )

    result = CondaExecutor(data_root=tmp_path, command_runner=fake_runner).install(build)

    assert result.status == "FAILED"
    assert result.exit_code == 2
    assert result.error_summary == "bad env"
    assert not (tmp_path / "environments" / build.id).exists()


def test_conda_executor_executes_hub_runner_with_limited_job_environment(
    tmp_path: Path,
) -> None:
    """Leaking ambient environment or bypassing hub_runner breaks reproducible execution."""
    fake_runner = FakeCommandRunner(
        CommandResult(
            returncode=0,
            stdout='@@HUB@@{"protocol_version":"1.0","type":"progress","percent":25}\n',
            stderr="",
        )
    )
    events: list[ProgressEvent] = []
    job = RunnerJob(
        id="job_123",
        runtime_type="conda-pack",
        workspace="jobs/job_123",
        job_path="jobs/job_123/job.json",
        manifest_path="plugins/plugin_build_123/plugin.yaml",
        plugin_root="plugins/plugin_build_123/plugin",
        result_path="jobs/job_123/result.json",
        environment_path="environments/plugin_build_123",
        timeout_seconds=30,
        cancel_requested=False,
    )

    result = CondaExecutor(
        data_root=tmp_path,
        command_runner=fake_runner,
        event_callback=events.append,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    assert result.exit_code == 0
    assert fake_runner.calls == [
        [
            str(tmp_path / "environments" / "plugin_build_123" / "bin" / "python"),
            "-m",
            "hub_runner",
            "--job",
            str(tmp_path / "jobs" / "job_123" / "job.json"),
            "--manifest",
            str(tmp_path / "plugins" / "plugin_build_123" / "plugin.yaml"),
            "--plugin-root",
            str(tmp_path / "plugins" / "plugin_build_123" / "plugin"),
            "--result",
            str(tmp_path / "jobs" / "job_123" / "result.json"),
        ]
    ]
    assert fake_runner.environments == [
        {
            "HUB_INPUT_DIR": str(tmp_path / "jobs" / "job_123" / "input"),
            "HUB_WORK_DIR": str(tmp_path / "jobs" / "job_123" / "work"),
            "HUB_OUTPUT_DIR": str(tmp_path / "jobs" / "job_123" / "output"),
            "HUB_LOG_DIR": str(tmp_path / "jobs" / "job_123" / "logs"),
            "PYTHONUNBUFFERED": "1",
        }
    ]
    assert events == [
        ProgressEvent(protocol_version="1.0", type="progress", percent=25)
    ]


def test_conda_executor_rejects_cancelled_job_before_start(tmp_path: Path) -> None:
    """A pre-cancelled claim should not launch plugin code."""
    fake_runner = FakeCommandRunner()
    job = RunnerJob(
        id="job_123",
        runtime_type="conda-pack",
        workspace="jobs/job_123",
        job_path="jobs/job_123/job.json",
        manifest_path="plugins/plugin_build_123/plugin.yaml",
        plugin_root="plugins/plugin_build_123/plugin",
        result_path="jobs/job_123/result.json",
        environment_path="environments/plugin_build_123",
        timeout_seconds=30,
        cancel_requested=True,
    )

    result = CondaExecutor(data_root=tmp_path, command_runner=fake_runner).execute(job)

    assert result.status is JobStatus.CANCELLED
    assert result.exit_code is None
    assert fake_runner.calls == []


def test_compose_adds_conda_runner_without_ports_or_docker_socket() -> None:
    """The conda runner must not be externally reachable or able to control Docker."""
    compose = Path("compose.yaml").read_text("utf-8")

    assert "hub-conda-runner:" in compose
    conda_section = compose.split("hub-conda-runner:", maxsplit=1)[1]
    conda_section = conda_section.split("\n  hub-docker-runner:", maxsplit=1)[0]
    assert "ports:" not in conda_section
    assert "/var/run/docker.sock" not in conda_section


def test_conda_runner_service_maps_claim_paths_relative_to_workspace() -> None:
    """Treating result.json as data-root-relative would complete Jobs without their result."""
    job = _job_from_payload(
        {
            "job_id": "job_123",
            "runtime_type": "conda-pack",
            "cancel_requested": False,
            "workspace": "jobs/job_123",
            "paths": {"job": "job.json", "result": "result.json"},
            "job": {"execution": {"timeout": 30}},
            "build": {
                "manifest": "plugins/build_123/plugin.yaml",
                "source": "plugins/build_123/plugin",
                "environment_path": "environments/build_123",
            },
        }
    )

    assert job.job_path == "jobs/job_123/job.json"
    assert job.result_path == "jobs/job_123/result.json"


def test_conda_runner_service_reconciles_interrupted_jobs_before_polling() -> None:
    """Skipping startup reconciliation leaves abandoned jobs permanently RUNNING."""
    calls: list[tuple[str, dict[str, object]]] = []

    def post(_: str, __: str, path: str, payload: dict[str, object]) -> None:
        calls.append((path, payload))

    _reconcile_interrupted_jobs("http://hub/internal/v1", "token", post=post)

    assert calls == [
        ("/jobs/reconcile", {"runtime_type": "conda-pack"}),
    ]


def _write_env_archive(path: Path) -> Path:
    tar_payload = io.BytesIO()
    with tarfile.open(fileobj=tar_payload, mode="w") as archive:
        for name, content in {
            "bin/conda-unpack": b"#!/bin/sh\n",
            "bin/python": b"#!/bin/sh\n",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(tar_payload.getvalue()))
    return path


def _relative_to_data(data_root: Path, path: Path) -> str:
    return path.resolve().relative_to(data_root.resolve()).as_posix()
