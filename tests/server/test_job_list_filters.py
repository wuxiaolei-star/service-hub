"""Job list endpoint tests: status/plugin filters, pagination, and total count."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Job, Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)

from tests.server.test_jobs_api import _manifest, _seed_build


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(_settings(tmp_path))) as test_client:
        bootstrap = test_client.app.state.settings.storage.root / "bootstrap-admin.json"
        payload = json.loads(bootstrap.read_text(encoding="utf-8"))
        login = test_client.post(
            "/api/v1/auth/login",
            json={"username": payload["username"], "password": payload["password"]},
        )
        assert login.status_code == 200, login.text
        test_client.headers.update({"Authorization": f"Bearer {login.json()['token']}"})
        yield test_client


def _upload_nc(client: TestClient) -> str:
    upload = client.post(
        "/api/v1/files",
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert upload.status_code == 201, upload.text
    return str(upload.json()["file_id"])


def _ensure_build(client: TestClient) -> None:
    """Seed the nc_to_shp docker Build once; _seed_build is not idempotent."""
    factory = client.app.state.session_factory
    with factory() as session:
        exists = (
            session.query(PluginBuild.id)
            .join(PluginVersion, PluginBuild.plugin_version_id == PluginVersion.id)
            .join(Plugin, PluginVersion.plugin_id == Plugin.id)
            .filter(Plugin.plugin_key == "nc_to_shp")
            .one_or_none()
        )
    if exists is None:
        _seed_build(client, runtime_type="docker")


def _create_job(
    client: TestClient,
    *,
    plugin_id: str = "nc_to_shp",
    version: str = "1.0.0",
    file_id: str | None = None,
) -> dict[str, object]:
    """Create one PENDING Job through the public API; seed Builds on demand."""
    if plugin_id == "nc_to_shp":
        _ensure_build(client)
    if file_id is None:
        file_id = _upload_nc(client)
    created = client.post(
        "/api/v1/jobs",
        json={
            "plugin_id": plugin_id,
            "version": version,
            "inputs": {"source_nc": file_id},
            "params": {},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def _seed_second_plugin(client: TestClient) -> None:
    """Seed an enabled docker Build for a second plugin (water_quality)."""
    factory = client.app.state.session_factory
    manifest = cast(dict[str, object], _manifest())
    manifest["plugin"] = {"id": "water_quality", "name": "Water Quality", "version": "2.0.0"}
    with factory() as session:
        plugin = Plugin(plugin_key="water_quality", name="Water Quality")
        version = PluginVersion(
            plugin=plugin,
            version="2.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=manifest,
            status="INSTALLED",
        )
        build = PluginBuild(
            build_key="plugin_build_water_quality",
            manifest_build_id="build-water-quality",
            plugin_version=version,
            target_os="linux",
            target_arch="amd64",
            runtime_type="docker",
            python_version="3.12",
            sdk_version="1.0",
            package_path="plugins/plugin_build_water_quality",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint="d" * 64,
            build_metadata_json={
                "schema_version": "1.0",
                "build_id": "build-water-quality",
                "plugin_id": "water_quality",
                "plugin_version": "2.0.0",
                "target": {"os": "linux", "arch": "amd64"},
                "python_version": "3.12",
                "runtime": {
                    "type": "docker",
                    "archive": "runtime/env.tar.zst",
                    "image": "water",
                    "digest": "d" * 64,
                },
                "sdk_version": "1.0.0",
                "source_sha256": "a" * 64,
            },
            status="ENABLED",
        )
        session.add_all([plugin, version, build])
        session.commit()


def _set_status(client: TestClient, job_key: str, status_value: str) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        job = session.query(Job).filter_by(job_key=job_key).one()
        job.status = status_value
        session.commit()


def test_list_jobs_without_filters_returns_newest_first_with_total(
    client: TestClient,
) -> None:
    first = _create_job(client)
    second = _create_job(client)

    response = client.get("/api/v1/jobs")

    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body["items"]] == [
        second["job_id"],
        first["job_id"],
    ]
    assert body["total"] == 2


def test_list_jobs_filters_by_status(client: TestClient) -> None:
    failed = _create_job(client)
    success = _create_job(client)
    _set_status(client, str(failed["job_id"]), "FAILED")
    _set_status(client, str(success["job_id"]), "SUCCESS")

    response = client.get("/api/v1/jobs", params={"status": "FAILED"})

    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body["items"]] == [failed["job_id"]]
    assert body["total"] == 1


def test_list_jobs_filters_by_multiple_statuses(client: TestClient) -> None:
    failed = _create_job(client)
    success = _create_job(client)
    pending = _create_job(client)
    _set_status(client, str(failed["job_id"]), "FAILED")
    _set_status(client, str(success["job_id"]), "SUCCESS")

    response = client.get(
        "/api/v1/jobs",
        params=[("status", "FAILED"), ("status", "SUCCESS")],
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["job_id"] for item in body["items"]} == {
        failed["job_id"],
        success["job_id"],
    }
    assert pending["job_id"] not in {item["job_id"] for item in body["items"]}
    assert body["total"] == 2


def test_list_jobs_unknown_status_returns_empty_page(client: TestClient) -> None:
    _create_job(client)  # PENDING

    response = client.get("/api/v1/jobs", params={"status": "RUNNING"})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_jobs_filters_by_plugin_id(client: TestClient) -> None:
    _seed_second_plugin(client)
    mine = _create_job(client)
    other = _create_job(client, plugin_id="water_quality", version="2.0.0")

    response = client.get("/api/v1/jobs", params={"plugin_id": "water_quality"})

    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body["items"]] == [other["job_id"]]
    assert mine["job_id"] != other["job_id"]
    assert body["total"] == 1

    combined = client.get(
        "/api/v1/jobs",
        params={"plugin_id": "water_quality", "status": "PENDING"},
    )
    assert combined.status_code == 200
    assert combined.json()["items"][0]["plugin_id"] == "water_quality"
    assert combined.json()["total"] == 1


def test_list_jobs_paginates_with_limit_and_offset(client: TestClient) -> None:
    first = _create_job(client)
    second = _create_job(client)
    third = _create_job(client)

    page = client.get("/api/v1/jobs", params={"limit": 1, "offset": 1})

    assert page.status_code == 200
    body = page.json()
    assert [item["job_id"] for item in body["items"]] == [second["job_id"]]
    assert body["total"] == 3
    assert {first["job_id"], third["job_id"]}.isdisjoint(
        {item["job_id"] for item in body["items"]}
    )

    beyond = client.get("/api/v1/jobs", params={"limit": 2, "offset": 3})
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3


def test_list_jobs_total_ignores_limit_and_offset(client: TestClient) -> None:
    for _ in range(3):
        job = _create_job(client)
        _set_status(client, str(job["job_id"]), "FAILED")

    response = client.get("/api/v1/jobs", params={"status": "FAILED", "limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


def test_list_jobs_rejects_out_of_range_pagination(client: TestClient) -> None:
    for query in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        response = client.get("/api/v1/jobs", params=query)
        assert response.status_code == 422, f"{query}: {response.text}"
