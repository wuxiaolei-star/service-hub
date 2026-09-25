"""Plugin-build list endpoint tests: pagination, filters, and the total count."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Plugin, PluginBuild, PluginVersion
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)

_SEQUENCE = count(1)


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


def _seed_build(
    client: TestClient,
    *,
    runtime_type: str,
    plugin_id: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Seed one Build under a fresh plugin key so seeds never collide."""
    plugin_id = plugin_id if plugin_id is not None else f"plugin_{next(_SEQUENCE)}"
    with client.app.state.session_factory() as session:
        plugin = Plugin(plugin_key=plugin_id, name=plugin_id)
        plugin_version = PluginVersion(
            plugin=plugin,
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json=_manifest(plugin_id),
            status="INSTALLED",
        )
        build = PluginBuild(
            build_key=f"plugin_build_{next(_SEQUENCE)}_{plugin_id}",
            manifest_build_id=f"build-{plugin_id}",
            plugin_version=plugin_version,
            target_os="linux",
            target_arch="amd64",
            runtime_type=runtime_type,
            python_version="3.12",
            sdk_version="1.0",
            package_path=f"plugins/plugin_build_{plugin_id}",
            package_sha256="b" * 64,
            source_sha256="a" * 64,
            runtime_archive_path="runtime/env.tar.zst",
            runtime_fingerprint="c" * 64,
            build_metadata_json={
                "schema_version": "1.0",
                "runtime": {
                    "type": runtime_type,
                    "archive": "runtime/env.tar.zst"
                    if runtime_type == "conda-pack"
                    else "image.tar.zst",
                    "fingerprint": "c" * 64,
                },
            },
            status="READY",
            created_at=created_at or datetime.now(UTC),
        )
        session.add_all([plugin, plugin_version, build])
        session.commit()
        return build.build_key


def _manifest(plugin_id: str) -> dict[str, object]:
    return {
        "spec_version": "1.0",
        "plugin": {"id": plugin_id, "name": plugin_id, "version": "1.0.0"},
        "sdk": {"version": "1.0"},
        "runtime": {"type": "process", "python": {"version": "3.12"}},
        "entrypoint": {"module": f"{plugin_id}.main", "function": "run"},
        "parameters": [],
        "inputs": [],
        "outputs": [],
        "execution": {"timeout": 30, "concurrency": 1},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }


def test_plugin_build_list_returns_total_with_default_window(
    client: TestClient,
) -> None:
    """The pre-pagination default of 100 is kept; total reports the real count."""
    for _ in range(101):
        _seed_build(client, runtime_type="conda-pack")

    builds = client.get("/api/v1/plugin-builds")

    assert builds.status_code == 200
    body = builds.json()
    assert len(body["items"]) == 100
    assert body["total"] == 101


def test_plugin_build_list_paginates_with_limit_and_offset(
    client: TestClient,
) -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    first = _seed_build(client, runtime_type="conda-pack", created_at=base)
    second = _seed_build(
        client, runtime_type="conda-pack", created_at=base + timedelta(minutes=1)
    )
    third = _seed_build(
        client, runtime_type="conda-pack", created_at=base + timedelta(minutes=2)
    )

    everything = client.get("/api/v1/plugin-builds")
    assert [item["build_id"] for item in everything.json()["items"]] == [
        third,
        second,
        first,
    ]

    # Newest-first page: [third, second, first], so offset 1 starts at second.
    page = client.get("/api/v1/plugin-builds", params={"limit": 2, "offset": 1})

    assert page.status_code == 200
    body = page.json()
    assert [item["build_id"] for item in body["items"]] == [second, first]
    assert body["total"] == 3

    beyond = client.get("/api/v1/plugin-builds", params={"limit": 2, "offset": 3})
    assert beyond.status_code == 200
    assert beyond.json()["items"] == []
    assert beyond.json()["total"] == 3


def test_plugin_build_list_filters_by_runtime_type_with_total(
    client: TestClient,
) -> None:
    conda_key = _seed_build(client, runtime_type="conda-pack")
    _seed_build(client, runtime_type="docker")

    builds = client.get("/api/v1/plugin-builds", params={"runtime_type": "conda-pack"})

    assert builds.status_code == 200
    body = builds.json()
    assert [item["build_id"] for item in body["items"]] == [conda_key]
    assert body["total"] == 1
    assert all(
        item["runtime_type"] == "conda-pack" for item in body["items"]
    )


def test_plugin_build_list_rejects_unknown_runtime_type(client: TestClient) -> None:
    response = client.get("/api/v1/plugin-builds", params={"runtime_type": "process"})

    assert response.status_code == 422


def test_plugin_build_list_total_ignores_limit_and_offset(client: TestClient) -> None:
    for _ in range(3):
        _seed_build(client, runtime_type="docker")

    response = client.get("/api/v1/plugin-builds", params={"limit": 2, "offset": 0})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3


def test_plugin_build_list_rejects_out_of_range_pagination(client: TestClient) -> None:
    for query in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        response = client.get("/api/v1/plugin-builds", params=query)
        assert response.status_code == 422, f"{query}: {response.text}"
