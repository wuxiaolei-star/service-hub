from __future__ import annotations

import io
import os
import signal
import stat
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import hub_runner.conda_executor as conda_executor_module
import pytest
import yaml
import zstandard
from hub_runner.conda_executor import CommandResult, CondaExecutor, RunnerBuild, RunnerJob
from python_hub_contracts import JobStatus, ProgressEvent

from deploy.runner import conda_runner_service
from deploy.runner.conda_runner_service import (
    _cancellation_checker,
    _job_from_payload,
    _reconcile_interrupted_jobs,
)


class FakeCommandRunner:
    def __init__(self, result: CommandResult | None = None) -> None:
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str]] = []
        self.preexec_fns: list[object | None] = []
        self.result = result or CommandResult(returncode=0, stdout="", stderr="")

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
        event_callback: object | None = None,
        cancellation_requested: object | None = None,
        preexec_fn: object | None = None,
    ) -> CommandResult:
        del cwd, timeout, event_callback, cancellation_requested
        self.calls.append([str(arg) for arg in args])
        self.environments.append(dict(env or {}))
        self.preexec_fns.append(preexec_fn)
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
    assert fake_runner.calls[0] == [str(env_root / "bin" / "conda-unpack")]
    assert fake_runner.calls[1] == [
        str(env_root / "bin" / "python"),
        "-c",
        "import hub_runner, python_hub_contracts, python_hub_sdk",
    ]
    assert "h5py" not in " ".join(fake_runner.calls[1])
    assert "osgeo" not in " ".join(fake_runner.calls[1])
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


def test_failed_duplicate_install_preserves_existing_ready_environment(
    tmp_path: Path,
) -> None:
    """A retried install must never delete an already promoted immutable environment."""
    archive = _write_env_archive(tmp_path / "env.tar.zst")
    existing = tmp_path / "environments" / "plugin_build_123"
    existing.mkdir(parents=True)
    marker = existing / "ready-marker"
    marker.write_text("ready", encoding="utf-8")
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
    )

    result = CondaExecutor(
        data_root=tmp_path, command_runner=FakeCommandRunner()
    ).install(build)

    assert result.status == "FAILED"
    assert marker.read_text("utf-8") == "ready"


def test_conda_executor_rejects_build_id_that_escapes_environments_root(
    tmp_path: Path,
) -> None:
    """A path-shaped Build ID must not promote executable files outside environments/."""
    archive = _write_env_archive(tmp_path / "env.tar.zst")
    build = RunnerBuild(
        id="../escaped-build",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
    )

    result = CondaExecutor(
        data_root=tmp_path, command_runner=FakeCommandRunner()
    ).install(build)

    assert result.status == "FAILED"
    assert not (tmp_path / "escaped-build").exists()


def test_conda_executor_limits_total_decompressed_environment_size(
    tmp_path: Path,
) -> None:
    """A small compressed archive must not expand without a configured safety bound."""
    archive = _write_env_archive(
        tmp_path / "env.tar.zst",
        files={
            "bin/conda-unpack": b"#!/bin/sh\n",
            "bin/python": b"#!/bin/sh\n",
            "payload": b"12345678",
        },
    )
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
    )

    result = CondaExecutor(
        data_root=tmp_path,
        command_runner=FakeCommandRunner(),
        max_extracted_size_bytes=16,
    ).install(build)

    assert result.status == "FAILED"
    assert "size limit" in (result.error_summary or "")
    assert not (tmp_path / "environments" / build.id).exists()


def test_conda_executor_rejects_hardlink_whose_archive_target_escapes_root(
    tmp_path: Path,
) -> None:
    """Tar hardlinks resolve from archive root, not from the link member's parent."""
    environments = tmp_path / "environments"
    environments.mkdir()
    outside = environments / "outside-target"
    outside.write_text("outside", encoding="utf-8")
    archive = _write_env_archive(
        tmp_path / "env.tar.zst",
        hardlinks={"nested/link": "../outside-target"},
    )
    build = RunnerBuild(
        id="plugin_build_123",
        runtime_type="conda-pack",
        runtime_archive=_relative_to_data(tmp_path, archive),
    )

    result = CondaExecutor(
        data_root=tmp_path, command_runner=FakeCommandRunner()
    ).install(build)

    assert result.status == "FAILED"
    assert outside.read_text("utf-8") == "outside"
    assert not (environments / build.id).exists()


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
        job_process_runner=fake_runner,
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


def test_conda_executor_exposes_proj_and_gdal_data_paths(tmp_path: Path) -> None:
    """PROJ/GDAL find their data only via env vars conda activation would set.

    Without GDAL_DATA/PROJ_DATA the first EPSG lookup inside the unpacked
    environment fails with "Cannot find proj.db", which surfaces as
    SHAPEFILE_WRITE_FAILED in the plugin.
    """
    fake_runner = FakeCommandRunner()
    env_root = tmp_path / "environments" / "plugin_build_123"
    (env_root / "share" / "gdal").mkdir(parents=True)
    (env_root / "share" / "proj").mkdir(parents=True)
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
        job_process_runner=fake_runner,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    assert fake_runner.environments[0]["GDAL_DATA"] == str(env_root / "share" / "gdal")
    assert fake_runner.environments[0]["PROJ_DATA"] == str(env_root / "share" / "proj")
    assert fake_runner.environments[0]["PROJ_LIB"] == str(env_root / "share" / "proj")


def test_conda_executor_injects_no_rlimits_without_resource_caps(
    tmp_path: Path,
) -> None:
    """B7/G7: a claim without caps must spawn the plugin exactly as before B7."""
    fake_runner = FakeCommandRunner()
    job = _conda_job()

    result = CondaExecutor(
        data_root=tmp_path,
        command_runner=fake_runner,
        job_process_runner=fake_runner,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    assert fake_runner.preexec_fns == [None]


def test_conda_executor_injects_rlimit_preexec_for_resolved_caps(
    tmp_path: Path,
) -> None:
    """A claim carrying resolved caps must reach the child via a preexec_fn."""
    fake_runner = FakeCommandRunner()
    job = _conda_job(memory_mb=2048, cpus=1.5)

    result = CondaExecutor(
        data_root=tmp_path,
        command_runner=fake_runner,
        job_process_runner=fake_runner,
    ).execute(job)

    assert result.status is JobStatus.SUCCESS
    (preexec,) = fake_runner.preexec_fns
    if os.name == "nt":
        # preexec_fn and the resource module do not exist on Windows; the
        # development platform intentionally keeps the unlimited behaviour.
        assert preexec is None
        return
    assert callable(preexec)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import resource;"
            "print(resource.getrlimit(resource.RLIMIT_AS)[0]);"
            "print(resource.getrlimit(resource.RLIMIT_CPU)[0])",
        ],
        capture_output=True,
        text=True,
        check=True,
        preexec_fn=preexec,
    )
    soft_memory, soft_cpu = (int(value) for value in probe.stdout.split())
    assert soft_memory == 2048 * 1024 * 1024
    # RLIMIT_CPU approximates the CPU share as cpus * timeout seconds.
    assert soft_cpu == int(1.5 * job.timeout_seconds)


@pytest.mark.skipif(os.name == "nt", reason="preexec_fn semantics are POSIX-only")
def test_job_process_applies_rlimits_in_spawned_plugin_child(tmp_path: Path) -> None:
    """The Popen wiring itself (not just the builder) must enforce the caps."""
    result = conda_executor_module._run_job_process(
        [
            sys.executable,
            "-c",
            "import resource;"
            "print(resource.getrlimit(resource.RLIMIT_AS)[0]);"
            "print(resource.getrlimit(resource.RLIMIT_CPU)[0])",
        ],
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
        timeout=10,
        event_callback=lambda event: None,
        cancellation_requested=lambda: False,
        preexec_fn=conda_executor_module._rlimit_preexec(
            _conda_job(memory_mb=1024, cpus=2)
        ),
    )

    soft_memory, soft_cpu = (int(value) for value in result.stdout.split())
    assert soft_memory == 1024 * 1024 * 1024
    # The preexec is built from the job's own timeout (30s), not Popen's.
    assert soft_cpu == 2 * 30


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


def test_job_process_forwards_event_before_child_can_exit(tmp_path: Path) -> None:
    """Buffering stdout until exit deadlocks plugins that depend on live event delivery."""
    gate = tmp_path / "event-forwarded"
    script = (
        "import pathlib,sys,time; "
        "print('@@HUB@@{\"protocol_version\":\"1.0\",\"type\":\"progress\",\"percent\":40}', "
        "flush=True); "
        "gate=pathlib.Path(sys.argv[1]); "
        "deadline=time.monotonic()+1.5; "
        "exec('while not gate.exists() and time.monotonic() < deadline:\\n time.sleep(0.01)'); "
        "raise SystemExit(0 if gate.exists() else 9)"
    )
    events: list[ProgressEvent] = []

    def forward(event: ProgressEvent) -> None:
        events.append(event)
        gate.write_text("forwarded", encoding="utf-8")

    result = conda_executor_module._run_job_process(
        [sys.executable, "-c", script, str(gate)],
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
        timeout=2,
        event_callback=forward,
        cancellation_requested=lambda: False,
    )

    assert result.returncode == 0
    assert result.termination_reason is None
    assert events == [
        ProgressEvent(protocol_version="1.0", type="progress", percent=40)
    ]


def test_job_process_observes_cancellation_while_running(tmp_path: Path) -> None:
    """Checking cancellation only at claim time leaves a running Job impossible to stop."""
    started = tmp_path / "started"
    script = (
        "import pathlib,sys,time; "
        "pathlib.Path(sys.argv[1]).write_text('started'); "
        "exec('while True:\\n time.sleep(0.05)')"
    )

    result = conda_executor_module._run_job_process(
        [sys.executable, "-c", script, str(started)],
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
        timeout=5,
        event_callback=lambda event: None,
        cancellation_requested=started.exists,
    )

    assert result.termination_reason == "cancelled"
    assert result.returncode is not None


def test_job_process_terminates_plugin_group_when_runner_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A runner shutdown must signal its live plugin group before propagating the exit."""
    class Process:
        def __init__(self) -> None:
            self.pid = 1234
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self.wait_calls = 0

        def poll(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("plugin", timeout)
            return 0

        def terminate(self) -> None:
            raise AssertionError("the process group should receive signals directly")

    process = Process()
    signals: list[int] = []
    monkeypatch.setattr(conda_executor_module.os, "name", "posix")
    monkeypatch.setattr(
        conda_executor_module.os,
        "killpg",
        lambda _, sig: signals.append(sig),
        raising=False,
    )
    monkeypatch.setattr(conda_executor_module.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(conda_executor_module.subprocess, "Popen", lambda *_, **__: process)

    def shutdown_requested() -> bool:
        raise SystemExit("runner shutdown")

    with pytest.raises(SystemExit, match="runner shutdown"):
        conda_executor_module._run_job_process(
            ["plugin"],
            cwd=tmp_path,
            env={"PYTHONUNBUFFERED": "1"},
            timeout=5,
            event_callback=lambda event: None,
            cancellation_requested=shutdown_requested,
        )

    assert signals == [signal.SIGTERM, 9]


def test_job_process_stops_when_live_event_forwarding_fails(tmp_path: Path) -> None:
    """An internal API failure must not orphan plugin code outside worker control."""
    script = (
        "import time; "
        "print('@@HUB@@{\"protocol_version\":\"1.0\",\"type\":\"progress\",\"percent\":50}', "
        "flush=True); time.sleep(1)"
    )

    def reject_event(_: ProgressEvent) -> None:
        raise RuntimeError("internal API unavailable")

    result = conda_executor_module._run_job_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
        timeout=5,
        event_callback=reject_event,
        cancellation_requested=lambda: False,
    )

    assert result.returncode != 0
    assert "event forwarding failed" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="Production process-group semantics are POSIX-only")
def test_job_process_cancellation_terminates_the_entire_process_group(
    tmp_path: Path,
) -> None:
    """Killing only the runner leaves plugin grandchildren executing after cancellation."""
    started = tmp_path / "grandchild-started"
    heartbeat = tmp_path / "heartbeat"
    grandchild = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "exec(\"while True:\\n p.open('a').write('x')\\n time.sleep(0.02)\")"
    )
    parent = (
        "import pathlib,subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[3],sys.argv[2]], "
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text('started'); "
        "exec('while True:\\n time.sleep(0.05)')"
    )

    result = conda_executor_module._run_job_process(
        [sys.executable, "-c", parent, str(started), str(heartbeat), grandchild],
        cwd=tmp_path,
        env={"PYTHONUNBUFFERED": "1"},
        timeout=5,
        event_callback=lambda event: None,
        cancellation_requested=started.exists,
    )

    assert result.termination_reason == "cancelled"
    size_after_stop = heartbeat.stat().st_size if heartbeat.exists() else 0
    time.sleep(0.15)
    assert (heartbeat.stat().st_size if heartbeat.exists() else 0) == size_after_stop


def test_unified_service_starts_the_conda_runner() -> None:
    """The shared Hub image must supervise the retained conda Runner entrypoint."""
    model = yaml.safe_load(Path("compose.yaml").read_text("utf-8"))
    supervisor = Path("deploy/service_hub/supervisord.conf").read_text("utf-8")

    service = model["services"]["service-hub"]
    assert set(model["services"]) == {"service-hub", "service-hub-web"}
    assert service["image"] == "python-service-hub:1.0.0-linux-amd64"
    assert service["volumes"].count("/var/run/docker.sock:/var/run/docker.sock") == 1
    assert "[program:conda-runner]" in supervisor
    assert "python -m deploy.runner.conda_runner_service" in supervisor


def test_conda_runner_retries_reconciliation_and_registers_shutdown_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient Hub startup and SIGTERM must not strand this runner's plugin processes."""
    reconciliations: list[None] = []
    startup_actions: list[object] = []
    registered_signals: list[tuple[int, object]] = []
    monkeypatch.setenv("HUB_INTERNAL_BASE_URL", "http://hub/internal/v1")
    monkeypatch.setenv("HUB_RUNNER_TOKEN", "test-token")
    monkeypatch.setenv("HUB_DATA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        conda_runner_service,
        "signal",
        type(
            "Signals",
            (),
            {
                "SIGTERM": signal.SIGTERM,
                "signal": staticmethod(
                    lambda signum, handler: registered_signals.append((signum, handler))
                ),
            },
        )(),
        raising=False,
    )
    monkeypatch.setattr(
        conda_runner_service,
        "_reconcile_interrupted_jobs",
        lambda *_: reconciliations.append(None),
    )

    def retry(action: Callable[[], None]) -> None:
        startup_actions.append(action)
        action()

    monkeypatch.setattr(conda_runner_service, "retry_startup", retry, raising=False)
    monkeypatch.setattr(
        conda_runner_service,
        "_post",
        lambda *_: (_ for _ in ()).throw(SystemExit("stop polling")),
    )

    with pytest.raises(SystemExit, match="stop polling"):
        conda_runner_service.main()

    assert len(startup_actions) == 1
    assert reconciliations == [None]
    assert registered_signals == [(signal.SIGTERM, conda_runner_service._terminate_cleanly)]


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
    assert job.memory_mb is None
    assert job.cpus is None


def test_conda_runner_service_maps_resource_caps_from_claim_payload() -> None:
    """B7: resolved caps ride the build claim into the executor's RunnerJob."""
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
                "memory_mb": 4096,
                "cpus": 0.75,
            },
        }
    )

    assert job.memory_mb == 4096
    assert job.cpus == 0.75


def test_conda_runner_service_reconciles_interrupted_jobs_before_polling() -> None:
    """Skipping startup reconciliation leaves abandoned jobs permanently RUNNING."""
    calls: list[tuple[str, dict[str, object]]] = []

    def post(_: str, __: str, path: str, payload: dict[str, object]) -> None:
        calls.append((path, payload))

    _reconcile_interrupted_jobs("http://hub/internal/v1", "token", post=post)

    assert calls == [
        ("/jobs/reconcile", {"runtime_type": "conda-pack"}),
    ]


def test_conda_runner_service_checks_live_cancellation_via_internal_api() -> None:
    """Without an internal status check the worker cannot observe cancellation after claim."""
    calls: list[tuple[str, dict[str, object]]] = []

    def post(_: str, __: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"job_id": "job_123", "cancel_requested": True}

    check = _cancellation_checker(
        "http://hub/internal/v1", "token", "job_123", post=post
    )

    assert check() is True
    assert calls == [
        ("/jobs/job_123/cancellation", {"runtime_type": "conda-pack"}),
    ]


def _conda_job(
    *, memory_mb: int | None = None, cpus: float | None = None
) -> RunnerJob:
    return RunnerJob(
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
        memory_mb=memory_mb,
        cpus=cpus,
    )


def _write_env_archive(
    path: Path,
    *,
    files: dict[str, bytes] | None = None,
    hardlinks: dict[str, str] | None = None,
) -> Path:
    tar_payload = io.BytesIO()
    with tarfile.open(fileobj=tar_payload, mode="w") as archive:
        archive_files = files or {
            "bin/conda-unpack": b"#!/bin/sh\n",
            "bin/python": b"#!/bin/sh\n",
        }
        for name, content in archive_files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(content))
        for name, target in (hardlinks or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.LNKTYPE
            info.linkname = target
            archive.addfile(info)
    compressor = zstandard.ZstdCompressor()
    path.write_bytes(compressor.compress(tar_payload.getvalue()))
    return path


def _relative_to_data(data_root: Path, path: Path) -> str:
    return path.resolve().relative_to(data_root.resolve()).as_posix()
