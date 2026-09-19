"""Version ordering contract tests for plugin summaries."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Plugin, PluginVersion
from hub_server.routers.plugins import _version_sort_key
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
def client(tmp_path: Path) -> Iterator[TestClient]:
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


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("9.0", (9, 0)),
        ("10.0", (10, 0)),
        ("1.0.0", (1, 0, 0)),
        ("1.0", (1, 0)),
        ("1.x", (1, 0)),
        ("x", (0,)),
    ],
)
def test_version_sort_key_parses_dotted_integers(
    version: str, expected: tuple[int, ...]
) -> None:
    assert _version_sort_key(version) == expected


def test_version_sort_key_orders_nine_below_ten() -> None:
    """String comparison would rank "9.0" above "10.0"; numeric parts must not."""
    assert _version_sort_key("9.0") < _version_sort_key("10.0")


def test_version_sort_key_pads_missing_components() -> None:
    """A shorter tuple must lose to its extended form, so 1.0.0 outranks 1.0."""
    assert _version_sort_key("1.0.0") > _version_sort_key("1.0")


def test_plugin_list_latest_version_uses_numeric_order(client: TestClient) -> None:
    """The public plugin list must report the numerically newest version."""
    _seed_plugin_version(client, version="9.0")
    _seed_plugin_version(client, version="10.0")

    response = client.get("/api/v1/plugins")

    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()["items"]}
    assert items["nc_to_shp"]["latest_version"] == "10.0"


def _seed_plugin_version(client: TestClient, *, version: str) -> None:
    """Insert one PluginVersion row, creating the Plugin on first use."""
    with client.app.state.session_factory() as session:
        plugin = session.query(Plugin).filter_by(plugin_key="nc_to_shp").one_or_none()
        if plugin is None:
            plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
            session.add(plugin)
        session.add(
            PluginVersion(
                plugin=plugin,
                version=version,
                spec_version="1.0",
                sdk_version="1.0",
                source_sha256="a" * 64,
                manifest_json={"plugin": {"id": "nc_to_shp", "version": version}},
                status="INSTALLED",
            )
        )
        session.commit()
