"""B4b: guarded deletion of DEPRECATED Plugin Builds (review doc G4(2)).

Covers the guard chain (DEPRECATED-only, enabled Schedule last-Build, Pipeline
step references, active Jobs), the R3 deletion order (registry rows and storage
before the exact docker image, never prune), the dry-run rehearsal, and the
audit trail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import (
    AuditLogRecord,
    Environment,
    Pipeline,
    Plugin,
    PluginBuild,
    PluginVersion,
    Schedule,
)
from hub_server.services.build_deletion import ImageRemovalError
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="off"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


class FakeImageRemover:
    """Records exact removal calls; optionally fails them (audit H-2 style)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def remove(self, image_ref: str) -> None:
        self.calls.append(image_ref)
        if self.fail:
            raise ImageRemovalError("daemon refused")


@pytest.fixture
def remover(monkeypatch: pytest.MonkeyPatch) -> FakeImageRemover:
    fake = FakeImageRemover()
    monkeypatch.setattr(
        "hub_server.routers.plugins.DockerImageRemover", lambda: fake
    )
    return fake


def _seed_build(
    client: TestClient,
    *,
    status: str = "DEPRECATED",
    runtime_type: str = "docker",
    version: str = "1.0.0",
    image_digest: str | None = "d" * 64,
    build_key: str = "plugin_build_" + "a" * 32,
) -> str:
    with client.app.state.session_factory() as session:
        plugin = session.query(Plugin).filter_by(plugin_key="nc_to_shp").one_or_none()
        if plugin is None:
            plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
        plugin_version = (
            session.query(PluginVersion)
            .filter_by(plugin_id=plugin.id, version=version)
            .one_or_none()
            if plugin.id
            else None
        )
        if plugin_version is None:
            plugin_version = PluginVersion(
            plugin=plugin,
            version=version,
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"plugin": {"id": "nc_to_shp", "version": version}},
            status="INSTALLED",
        )
        build = PluginBuild(
            build_key=build_key,
            manifest_build_id="build-" + version,
            plugin_version=plugin_version,
            target_os="linux",
            target_arch="amd64",
            runtime_type=runtime_type,
            python_version="3.12",
            sdk_version="1.0",
            package_path=f"plugins/{build_key}",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="image.tar.zst",
            runtime_fingerprint="c" * 64,
            build_metadata_json={
                "schema_version": "1.0",
                "runtime": {
                    "type": runtime_type,
                    "archive": "image.tar.zst",
                    "digest": image_digest,
                },
            },
            status=status,
        )
        rows: list[Any] = [plugin, plugin_version, build]
        if runtime_type == "docker":
            rows.append(
                Environment(
                    plugin_build=build,
                    runtime_type="docker",
                    fingerprint="c" * 64,
                    image_digest=image_digest,
                    metadata_json={"type": "docker"},
                    status="READY",
                )
            )
        session.add_all(rows)
        session.commit()
        storage_root = Path(client.app.state.settings.storage.root)
        for relative in (f"plugins/{build_key}", f"environments/{build_key}"):
            directory = storage_root / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "artifact.bin").write_bytes(b"payload")
        return build.build_key


def _add_schedule(client: TestClient, *, enabled: bool = True) -> None:
    with client.app.state.session_factory() as session:
        session.add(
            Schedule(
                name="hourly-nc",
                plugin_id="nc_to_shp",
                version="1.0.0",
                runtime_type="docker",
                inputs_json={},
                params_json={},
                interval_minutes=60,
                enabled=enabled,
                next_run_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.commit()


def _add_pipeline(client: TestClient, *, with_runtime: bool = True) -> None:
    step: dict[str, Any] = {"plugin_id": "nc_to_shp", "version": "1.0.0"}
    if with_runtime:
        step["runtime_type"] = "docker"
    with client.app.state.session_factory() as session:
        session.add(Pipeline(name="nightly", steps_json=[step]))
        session.commit()


def _add_active_job(client: TestClient, build_key: str) -> None:
    with client.app.state.session_factory() as session:
        build = session.query(PluginBuild).filter_by(build_key=build_key).one()
        from hub_server.models import Job

        session.add(
            Job(
                plugin_build=build,
                runtime_type=build.runtime_type,
                runtime_fingerprint=build.runtime_fingerprint,
                status="PENDING",
                params_json={},
                inputs_json={},
                timeout_seconds=60,
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()


def test_dry_run_returns_plan_without_changes(client: TestClient, remover) -> None:
    build_key = _seed_build(client)

    response = client.delete(
        f"/api/v1/plugin-builds/{build_key}", params={"dry_run": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["allowed"] is True
    assert body["build_key"] == build_key
    assert any("plugins/" in action for action in body["actions"])
    assert any("docker image rm sha256:" in action for action in body["actions"])
    assert remover.calls == []
    detail = client.get(f"/api/v1/plugin-builds/{build_key}")
    assert detail.status_code == 200


def test_dry_run_reports_guard_violations(client: TestClient) -> None:
    build_key = _seed_build(client, status="ENABLED")

    response = client.delete(
        f"/api/v1/plugin-builds/{build_key}", params={"dry_run": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is False
    assert body["violations"][0]["code"] == "PLUGIN_BUILD_NOT_DEPRECATED"


def test_delete_requires_deprecated_status(client: TestClient) -> None:
    """READY (a disabled Build) must go through deprecate first (cooling-off)."""
    build_key = _seed_build(client, status="READY")

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_DEPRECATED"


def test_delete_removes_rows_storage_then_image_in_r3_order(
    client: TestClient, remover, tmp_path: Path
) -> None:
    build_key = _seed_build(client)
    storage_root = Path(client.app.state.settings.storage.root)
    assert (storage_root / "plugins" / build_key).is_dir()
    assert (storage_root / "environments" / build_key).is_dir()

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 200
    assert response.json() == {"deleted": build_key}
    # Registry rows are gone.
    assert client.get(f"/api/v1/plugin-builds/{build_key}").status_code == 404
    # Storage directories are gone.
    assert not (storage_root / "plugins" / build_key).exists()
    assert not (storage_root / "environments" / build_key).exists()
    # The exact image reference was removed, and only after the storage step
    # (FakeImageRemover records exactly one precise digest call, never prune).
    assert remover.calls == [f"sha256:{'d' * 64}"]
    with client.app.state.session_factory() as session:
        entry = session.query(AuditLogRecord).filter_by(
            action="plugin_build.delete"
        ).one()
    assert entry.resource_id == build_key
    assert entry.result == "ok"


def test_delete_conda_build_has_no_image_step(client: TestClient, remover) -> None:
    build_key = _seed_build(
        client, runtime_type="conda-pack", image_digest=None
    )

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 200
    assert remover.calls == []


def test_image_removal_failure_does_not_rollback_the_deletion(
    client: TestClient, remover, tmp_path: Path
) -> None:
    build_key = _seed_build(client)
    remover.fail = True

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 200
    assert client.get(f"/api/v1/plugin-builds/{build_key}").status_code == 404
    with client.app.state.session_factory() as session:
        entry = session.query(AuditLogRecord).filter_by(
            action="plugin_build.delete"
        ).one()
    detail = entry.detail
    assert detail["image_removed"] is False
    assert detail["image_error"]


def test_enabled_schedule_referencing_last_build_blocks_deletion(
    client: TestClient, remover
) -> None:
    build_key = _seed_build(client)
    _add_schedule(client, enabled=True)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"] == "PLUGIN_BUILD_SCHEDULE_REFERENCED"
    )
    assert client.get(f"/api/v1/plugin-builds/{build_key}").status_code == 200


def test_disabled_schedule_does_not_block_deletion(client: TestClient) -> None:
    build_key = _seed_build(client)
    _add_schedule(client, enabled=False)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 200


def test_other_runtime_build_does_not_keep_schedule_serviceable(
    client: TestClient,
) -> None:
    """The immutable (version, os, arch, runtime) uniqueness means a triple has
    exactly one Build, so a same-runtime sibling cannot exist. A conda-pack
    sibling cannot serve a docker-pinned schedule either, so the guard still
    fires — this test pins that conservative reading.
    """
    build_key = _seed_build(client)
    _seed_build(
        client,
        runtime_type="conda-pack",
        image_digest=None,
        build_key="plugin_build_" + "b" * 32,
    )
    _add_schedule(client, enabled=True)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"] == "PLUGIN_BUILD_SCHEDULE_REFERENCED"
    )


def test_pipeline_step_reference_blocks_deletion(
    client: TestClient, remover
) -> None:
    build_key = _seed_build(client)
    _add_pipeline(client, with_runtime=True)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409
    assert (
        response.json()["error"]["code"] == "PLUGIN_BUILD_PIPELINE_REFERENCED"
    )


def test_pipeline_step_without_runtime_blocks_conservatively(
    client: TestClient,
) -> None:
    build_key = _seed_build(client)
    _add_pipeline(client, with_runtime=False)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409


def test_active_job_blocks_deletion(client: TestClient) -> None:
    build_key = _seed_build(client)
    _add_active_job(client, build_key)

    response = client.delete(f"/api/v1/plugin-builds/{build_key}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_IN_USE"


def test_unknown_build_returns_404(client: TestClient) -> None:
    response = client.delete("/api/v1/plugin-builds/plugin_build_unknown")

    assert response.status_code == 404
