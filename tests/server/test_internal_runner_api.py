"""Internal runner coordination API and staged installation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Environment, Job, PluginBuild, RunnerOperation
from hub_server.repositories import HubRepository
from hub_server.services.archives import VerifiedPluginPackage
from hub_server.services.plugins import PluginService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerResourceLimitsSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)
from hub_server.storage import LocalStorage
from python_hub_contracts import PluginBuildManifest, RuntimeType, load_plugin_manifest
from sqlalchemy import select
from sqlalchemy.orm import Session


class _Default:
    """Sentinel distinguishing 'unset' from an explicit null resource_limits node."""


_DEFAULT = _Default()


def _settings(
    tmp_path: Path,
    *,
    resource_limits: RunnerResourceLimitsSettings | _Default | None = _DEFAULT,
) -> HubSettings:
    resolved_limits = (
        RunnerResourceLimitsSettings() if resource_limits is _DEFAULT else resource_limits
    )
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(
            shared_token="runner-test-secret",
            poll_interval_seconds=1,
            resource_limits=resolved_limits,
        ),
        auth=AuthSettings(mode="off"),
        platform_os="linux",
        platform_arch="amd64",
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        yield test_client


def _runner_post(
    client: TestClient, path: str, body: dict[str, object], *, token: str = "runner-test-secret"
):
    return client.post(path, headers={"X-Hub-Runner-Token": token}, json=body)


def _manifest(runtime_type: RuntimeType) -> tuple[object, PluginBuildManifest]:
    fixtures = Path(__file__).parents[1] / "fixtures"
    plugin = load_plugin_manifest(fixtures / "valid-plugin.yaml")
    raw = json.loads((fixtures / "valid-build.json").read_text("utf-8"))
    raw["target"]["arch"] = "amd64"
    if runtime_type == "docker":
        raw["build_id"] = "package-docker-build"
        raw["runtime"] = {
            "type": "docker",
            "archive": "image.tar.zst",
            "image": "nc-to-shp:1.0.0",
            "digest": "1" * 64,
        }
    return plugin, PluginBuildManifest.model_validate(raw)


def _seed_build(session: Session, runtime_type: RuntimeType) -> PluginBuild:
    plugin, build = _manifest(runtime_type)
    record = HubRepository(session).create_build_installation(
        plugin_manifest=plugin,
        build_manifest=build,
        package_sha256=("d" if runtime_type == "docker" else "c") * 64,
        package_path=f"plugins/seed-{runtime_type}",
    )
    record.runtime_archive_path = f"plugins/{record.build_key}/{build.runtime.archive}"
    assert record.environment is not None
    if runtime_type == "conda-pack":
        record.environment.environment_path = f"environments/{record.build_key}"
    session.commit()
    return record


def test_unpublished_installation_cannot_be_claimed(client: TestClient) -> None:
    plugin, manifest = _manifest("docker")
    with client.app.state.session_factory() as publisher:
        record = HubRepository(publisher).create_build_installation(
            plugin_manifest=plugin, build_manifest=manifest, package_sha256="a" * 64
        )
        with client.app.state.session_factory() as worker:
            assert HubRepository(worker).claim_operation("docker") is None
        record.package_path = f"plugins/{record.build_key}"
        record.runtime_archive_path = f"{record.package_path}/image.tar.zst"
        publisher.commit()
        with client.app.state.session_factory() as worker:
            assert HubRepository(worker).claim_operation("docker") is not None


@pytest.mark.parametrize("runtime_type", ["docker", "conda-pack"])
def test_reconcile_terminates_interrupted_installations(client: TestClient, runtime_type) -> None:
    with client.app.state.session_factory() as session:
        build = _seed_build(session, runtime_type)
        operation = HubRepository(session).claim_operation(runtime_type)
        key = build.build_key
        assert operation is not None
    response = _runner_post(client, "/internal/v1/jobs/reconcile", {"runtime_type": runtime_type})
    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        build = session.scalar(select(PluginBuild).where(PluginBuild.build_key == key))
        assert build.status == "FAILED"
        assert build.environment.status == "FAILED"
        assert session.scalar(select(RunnerOperation)).status == "FAILED"


def _seed_pending_job(session: Session, runtime_type: RuntimeType) -> Job:
    build = _seed_build(session, runtime_type)
    build.status = "ENABLED"
    assert build.environment is not None
    build.environment.status = "READY"
    job = Job(
        plugin_build=build,
        runtime_type=runtime_type,
        runtime_fingerprint=build.runtime_fingerprint,
        status="PENDING",
        params_json={"start_time": 1},
        inputs_json={"source_nc": "file_input"},
        job_json={
            "protocol_version": "1.0",
            "job": {"id": "job-placeholder", "created_at": "2026-09-06T00:00:00Z"},
            "plugin": {
                "id": "nc_to_shp",
                "version": "1.0.0",
                "build_id": build.build_key,
            },
            "params": {"start_time": 1},
            "inputs": {
                "source_nc": {
                    "id": "file_input",
                    "name": "source.nc",
                    "path": "input/source.nc",
                    "size": 3,
                    "extension": ".nc",
                    "sha256": "f" * 64,
                }
            },
            "directories": {
                "input": "input",
                "work": "work",
                "output": "output",
                "logs": "logs",
            },
            "execution": {"timeout": 60},
        },
        workspace_path="jobs/job-placeholder",
        timeout_seconds=60,
    )
    session.add(job)
    session.flush()
    persisted_job_json = dict(job.job_json)
    persisted_job_json["job"] = {**persisted_job_json["job"], "id": job.job_key}
    job.job_json = persisted_job_json
    job.workspace_path = f"jobs/{job.job_key}"
    session.commit()
    return job


def test_internal_claim_rejects_missing_or_wrong_token_without_leaking_secret(
    client: TestClient,
) -> None:
    """Removing runner authentication would let any network peer claim private work."""
    missing = client.post("/internal/v1/jobs/claim", json={"runtime_type": "docker"})
    wrong = _runner_post(
        client,
        "/internal/v1/jobs/claim",
        {"runtime_type": "docker"},
        token="wrong-runner-test-secret",
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "RUNNER_AUTH_FAILED"
    assert "runner-test-secret" not in wrong.text
    assert "wrong-runner-test-secret" not in wrong.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/internal/v1/operations/claim", {"runtime_type": "docker"}),
        (
            "/internal/v1/operations/operation_missing/complete",
            {"runtime_type": "docker", "status": "FAILED"},
        ),
        ("/internal/v1/jobs/claim", {"runtime_type": "docker"}),
        (
            "/internal/v1/jobs/job_missing/cancellation",
            {"runtime_type": "docker"},
        ),
        (
            "/internal/v1/jobs/job_missing/events",
            {
                "runtime_type": "docker",
                "event": {
                    "protocol_version": "1.0",
                    "type": "log",
                    "level": "INFO",
                    "message": "started",
                },
            },
        ),
        (
            "/internal/v1/jobs/job_missing/complete",
            {
                "runtime_type": "docker",
                "result": {
                    "protocol_version": "1.0",
                    "job_id": "job_missing",
                    "status": "FAILED",
                    "started_at": "2026-09-06T00:00:00Z",
                    "finished_at": "2026-09-06T00:00:01Z",
                    "duration_ms": 1000,
                    "message": "failed",
                    "data": {},
                    "files": [],
                    "error": {
                        "type": "runtime",
                        "code": "PLUGIN_FAILED",
                        "message": "failed",
                    },
                },
            },
        ),
    ],
)
def test_every_internal_endpoint_requires_runner_token(
    client: TestClient, path: str, body: dict[str, object]
) -> None:
    """Forgetting authentication on one mutating route opens the private control plane."""
    response = client.post(path, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "RUNNER_AUTH_FAILED"


def test_matching_runner_claims_only_its_runtime_with_relative_paths(
    client: TestClient,
) -> None:
    """Dropping the runtime predicate or returning host paths violates worker isolation."""
    with client.app.state.session_factory() as session:
        conda = _seed_pending_job(session, "conda-pack")
        docker = _seed_pending_job(session, "docker")
        conda_key = conda.job_key
        docker_key = docker.job_key

    response = _runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "docker"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == docker_key
    assert payload["runtime_type"] == "docker"
    assert payload["job"]["job"]["id"] == docker_key
    assert payload["workspace"] == f"jobs/{docker_key}"
    assert payload["paths"] == {"job": "job.json", "result": "result.json"}
    assert payload["build"]["runtime"]["type"] == "docker"
    assert payload["build"]["image_digest"] == "1" * 64
    # B7/G7/R6: the fixture manifest declares no resource caps, so the claim
    # must carry the platform defaults from settings.runner.resource_limits.
    assert payload["build"]["memory_mb"] == 2048
    assert payload["build"]["cpus"] == 1.5
    assert not any(value.startswith(("/", "C:")) for value in _all_strings(payload))
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Job).where(Job.job_key == conda_key)).status == "PENDING"
        assert session.scalar(select(Job).where(Job.job_key == docker_key)).status == "PREPARING"


def test_manifest_declared_resource_limits_override_platform_defaults(
    client: TestClient,
) -> None:
    """B7 priority: manifest explicit declaration > platform default."""
    with client.app.state.session_factory() as session:
        job = _seed_pending_job(session, "docker")
        job_key = job.job_key
        version = job.plugin_build.plugin_version
        version.manifest_json = {
            **version.manifest_json,
            "execution": {
                **version.manifest_json["execution"],
                "memory_mb": 8192,
                "cpus": 0.75,
            },
        }
        session.commit()

    payload = _runner_post(
        client, "/internal/v1/jobs/claim", {"runtime_type": "docker"}
    ).json()

    assert payload["job_id"] == job_key
    assert payload["build"]["memory_mb"] == 8192
    assert payload["build"]["cpus"] == 0.75


def test_disabled_platform_resource_limits_leave_undeclared_plugins_uncapped(
    tmp_path: Path,
) -> None:
    """runner.resource_limits: null opts the whole platform out of default caps."""
    with TestClient(create_app(_settings(tmp_path, resource_limits=None))) as test_client:
        with test_client.app.state.session_factory() as session:
            job = _seed_pending_job(session, "docker")
            job_key = job.job_key

        payload = _runner_post(
            test_client, "/internal/v1/jobs/claim", {"runtime_type": "docker"}
        ).json()

        assert payload["job_id"] == job_key
        assert payload["build"]["memory_mb"] is None
        assert payload["build"]["cpus"] is None


def test_matching_runner_claims_and_completes_installation_atomically(
    client: TestClient,
) -> None:
    """Completing only the operation can leave Build and Environment states inconsistent."""
    with client.app.state.session_factory() as session:
        build = _seed_build(session, "conda-pack")
        operation = session.scalar(
            select(RunnerOperation).where(RunnerOperation.plugin_build_id == build.id)
        )
        assert operation is not None
        operation_id = operation.operation_key
        build_id = build.build_key

    claim = _runner_post(client, "/internal/v1/operations/claim", {"runtime_type": "conda-pack"})
    assert claim.status_code == 200
    assert claim.json()["operation_id"] == operation_id
    assert claim.json()["build"]["runtime_archive"].startswith("plugins/")

    completed = _runner_post(
        client,
        f"/internal/v1/operations/{operation_id}/complete",
        {
            "runtime_type": "conda-pack",
            "status": "SUCCESS",
            "environment_path": f"environments/{build_id}",
            "metadata": {"healthcheck": "ok"},
        },
    )

    assert completed.status_code == 200
    assert completed.json() == {"id": operation_id, "status": "SUCCESS"}
    with client.app.state.session_factory() as session:
        persisted_build = session.scalar(
            select(PluginBuild).where(PluginBuild.build_key == build_id)
        )
        environment = session.scalar(
            select(Environment).where(Environment.plugin_build_id == persisted_build.id)
        )
        operation = session.scalar(
            select(RunnerOperation).where(RunnerOperation.operation_key == operation_id)
        )
        assert persisted_build.status == "READY"
        assert environment.status == "READY"
        assert environment.environment_path == f"environments/{build_id}"
        assert operation.status == "SUCCESS"


def test_stage_installation_moves_uuid_staging_to_public_build_directory(
    tmp_path: Path,
) -> None:
    """Using the package manifest ID for storage breaks the generated Build identity boundary."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app), app.state.session_factory() as session:
        plugin, build_manifest = _manifest("docker")
        staging = settings.storage.root / "plugins" / ".staging" / ("a" * 32)
        (staging / "plugin").mkdir(parents=True)
        (staging / "plugin.yaml").write_text("manifest", encoding="utf-8")
        (staging / "plugin" / "main.py").write_text("pass\n", encoding="utf-8")
        (staging / "image.tar.zst").write_bytes(b"image")
        verified = VerifiedPluginPackage(
            manifest=plugin,
            build=build_manifest,
            source_dir=staging / "plugin",
            archive=staging / "image.tar.zst",
        )

        build = PluginService(
            session,
            LocalStorage(settings.storage.root),
            platform_os="linux",
            platform_arch="amd64",
        ).stage_installation(verified, package_sha256="a" * 64)

        destination = settings.storage.root / "plugins" / build.build_key
        assert not staging.exists()
        assert destination.is_dir()
        assert build.package_path == f"plugins/{build.build_key}"
        assert build.runtime_archive_path == f"plugins/{build.build_key}/image.tar.zst"
        operation = session.scalar(
            select(RunnerOperation).where(RunnerOperation.plugin_build_id == build.id)
        )
        assert operation is not None
        assert operation.payload_json["source_path"] == f"plugins/{build.build_key}/plugin"


def test_job_event_starts_job_and_completion_records_terminal_state(
    client: TestClient,
) -> None:
    """Ignoring events or completion leaves claimed jobs permanently in PREPARING."""
    with client.app.state.session_factory() as session:
        job = _seed_pending_job(session, "docker")
        job_key = job.job_key
    claim = _runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "docker"})
    assert claim.status_code == 200

    event = _runner_post(
        client,
        f"/internal/v1/jobs/{job_key}/events",
        {
            "runtime_type": "docker",
            "event": {
                "protocol_version": "1.0",
                "type": "progress",
                "percent": 25,
                "message": "working",
            },
        },
    )
    # The fixture manifest declares a required files output (result_files);
    # a SUCCESS completion must register it to satisfy the G6 output contract.
    output = client.app.state.storage.open_relative(f"jobs/{job_key}/output/shapefile.zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"shapefile-bytes")
    now = datetime.now(UTC)
    completion = _runner_post(
        client,
        f"/internal/v1/jobs/{job_key}/complete",
        {
            "runtime_type": "docker",
            "exit_code": 0,
            "result": {
                "protocol_version": "1.0",
                "job_id": job_key,
                "status": "SUCCESS",
                "started_at": (now - timedelta(seconds=1)).isoformat(),
                "finished_at": now.isoformat(),
                "duration_ms": 1000,
                "message": "complete",
                "data": {},
                "files": [
                    {
                        "name": "result_files",
                        "path": "shapefile.zip",
                        "format": "zip",
                        "size": len(b"shapefile-bytes"),
                        "sha256": "0" * 64,
                    }
                ],
                "error": None,
            },
        },
    )

    assert event.json() == {"id": job_key, "status": "RUNNING"}
    assert completion.json() == {"id": job_key, "status": "SUCCESS"}
    settings = client.app.state.settings
    event_log = settings.storage.root / "jobs" / job_key / "logs" / "events.jsonl"
    assert json.loads(event_log.read_text("utf-8"))["percent"] == 25
    with client.app.state.session_factory() as session:
        persisted = session.scalar(select(Job).where(Job.job_key == job_key))
        assert persisted.status == "SUCCESS"
        assert persisted.exit_code == 0


def test_job_completion_prefixes_plugin_errors_with_their_stable_code(
    client: TestClient,
) -> None:
    """G8/B8: the completion path stores "CODE: message" summaries.

    Normalizing through ``services.failures.failure_summary`` is what makes the
    zero-migration failure classification derivable from the stored row.
    """
    with client.app.state.session_factory() as session:
        job = _seed_pending_job(session, "docker")
        job_key = job.job_key
    claim = _runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "docker"})
    assert claim.status_code == 200

    now = datetime.now(UTC)
    completion = _runner_post(
        client,
        f"/internal/v1/jobs/{job_key}/complete",
        {
            "runtime_type": "docker",
            "exit_code": 1,
            "result": {
                "protocol_version": "1.0",
                "job_id": job_key,
                "status": "FAILED",
                "started_at": (now - timedelta(seconds=2)).isoformat(),
                "finished_at": now.isoformat(),
                "duration_ms": 2000,
                "message": "plugin failed",
                "data": {},
                "files": [],
                "error": {
                    "type": "PluginExecutionError",
                    "code": "NC_READ_FAILED",
                    "message": "NetCDF 读取失败",
                },
            },
        },
    )

    assert completion.status_code == 200
    assert completion.json() == {"id": job_key, "status": "FAILED"}
    with client.app.state.session_factory() as session:
        persisted = session.scalar(select(Job).where(Job.job_key == job_key))
        assert persisted.status == "FAILED"
        assert persisted.error_summary == "NC_READ_FAILED: NetCDF 读取失败"
        assert persisted.exit_code == 1
        assert persisted.finished_at is not None


def test_runtime_reconcile_fails_only_matching_interrupted_jobs(
    client: TestClient,
) -> None:
    """Runner restart recovery must not fail pending or other-runtime jobs."""
    with client.app.state.session_factory() as session:
        conda_interrupted = _seed_pending_job(session, "conda-pack")
        docker_interrupted = _seed_pending_job(session, "docker")
        conda_interrupted.status = "RUNNING"
        docker_interrupted.status = "PREPARING"
        session.commit()
        conda_interrupted_key = conda_interrupted.job_key
        docker_interrupted_key = docker_interrupted.job_key

    response = _runner_post(
        client,
        "/internal/v1/jobs/reconcile",
        {"runtime_type": "conda-pack"},
    )

    assert response.status_code == 200
    assert response.json() == {"runtime_type": "conda-pack", "failed_jobs": 1}
    with client.app.state.session_factory() as session:
        conda_job = session.scalar(select(Job).where(Job.job_key == conda_interrupted_key))
        docker_job = session.scalar(select(Job).where(Job.job_key == docker_interrupted_key))
        assert conda_job.status == "FAILED"
        assert conda_job.error_summary == "HUB_RESTARTED"
        assert docker_job.status == "PREPARING"


def test_runner_can_observe_cancellation_requested_after_job_claim(
    client: TestClient,
) -> None:
    """A claimed worker needs a private read path for cancellation requested later."""
    with client.app.state.session_factory() as session:
        job = _seed_pending_job(session, "conda-pack")
        job_key = job.job_key
    claim = _runner_post(client, "/internal/v1/jobs/claim", {"runtime_type": "conda-pack"})
    assert claim.status_code == 200
    with client.app.state.session_factory() as session:
        persisted = session.scalar(select(Job).where(Job.job_key == job_key))
        persisted.cancel_requested = True
        session.commit()

    response = _runner_post(
        client,
        f"/internal/v1/jobs/{job_key}/cancellation",
        {"runtime_type": "conda-pack"},
    )

    assert response.status_code == 200
    assert response.json() == {"job_id": job_key, "cancel_requested": True}


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for child in value for item in _all_strings(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _all_strings(child)]
    return []
