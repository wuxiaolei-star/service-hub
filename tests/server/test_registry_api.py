"""Registry browse/download and by-sha256 dedup contract tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import Environment, Plugin, PluginBuild, PluginVersion, UserRecord
from hub_server.routers.registry import router as registry_router
from hub_server.services.auth import AuthService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    QuotasSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=10240),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),  # type: ignore[arg-type]
        quotas=QuotasSettings(),
    )


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(registry_router, prefix="/api/v1")
    with TestClient(app) as test_client:
        yield test_client


def _admin_headers(client: TestClient) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == "admin").one()
        service = AuthService(session)
        _record, token = service.create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _user_headers(client: TestClient, headers: dict[str, str], role: str) -> dict[str, str]:
    username = f"{role}-user"
    created = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": username, "password": "long-enough-pass", "role": role},
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "long-enough-pass"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


def _seed_build(
    client: TestClient,
    *,
    plugin_id: str,
    version: str,
    runtime_type: str,
    status: str = "ENABLED",
    with_package: bool = True,
) -> str:
    """Insert one production-shaped Build and its stored package payload."""
    build_key = f"plugin_build_{plugin_id}_{version}_{runtime_type}"
    build_key = build_key.replace(".", "_").replace("-", "_")
    suffix = "runtime/env.tar.zst" if runtime_type == "conda-pack" else "image.tar.zst"
    package_payload = f"package:{plugin_id}:{version}:{runtime_type}".encode()
    with client.app.state.session_factory() as session:
        runtime_metadata = (
            {"type": "conda-pack", "archive": suffix, "fingerprint": "c" * 64}
            if runtime_type == "conda-pack"
            else {"type": "docker", "archive": suffix, "image": plugin_id, "digest": "d" * 64}
        )
        fingerprint = str(runtime_metadata.get("fingerprint") or runtime_metadata["digest"])
        plugin = session.query(Plugin).filter_by(plugin_key=plugin_id).one_or_none()
        if plugin is None:
            plugin = Plugin(plugin_key=plugin_id, name=f"{plugin_id} name")
            session.add(plugin)
        plugin_version = (
            session.query(PluginVersion)
            .filter_by(plugin=plugin, version=version)
            .one_or_none()
        )
        if plugin_version is None:
            plugin_version = PluginVersion(
                plugin=plugin,
                version=version,
                spec_version="1.0",
                sdk_version="1.0",
                source_sha256="a" * 64,
                manifest_json={},
                status="INSTALLED",
            )
            session.add(plugin_version)
        build = PluginBuild(
            build_key=build_key,
            manifest_build_id=f"build-{plugin_id}-{version}",
            plugin_version=plugin_version,
            target_os="linux",
            target_arch="amd64",
            runtime_type=runtime_type,
            python_version="3.12",
            sdk_version="1.0",
            package_path=f"plugins/{build_key}",
            package_sha256=hashlib.sha256(package_payload).hexdigest(),
            source_sha256="a" * 64,
            runtime_archive_path=f"plugins/{build_key}/{suffix}",
            runtime_fingerprint=fingerprint,
            build_metadata_json={
                "schema_version": "1.0",
                "runtime": runtime_metadata,
            },
            status=status,
            created_at=datetime.now(UTC),
        )
        session.add(
            Environment(
                plugin_build=build,
                runtime_type=runtime_type,
                fingerprint=fingerprint,
                metadata_json=runtime_metadata,
                status=status,
            )
        )
        session.add(build)
        session.commit()
    if with_package:
        storage_root = Path(client.app.state.settings.storage.root)
        archive = storage_root / "plugins" / build_key / Path(*suffix.split("/"))
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(package_payload)
    return build_key


def test_registry_list_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/registry/plugins")
    assert response.status_code == 401


def test_registry_list_returns_sorted_flat_items(client: TestClient) -> None:
    headers = _admin_headers(client)
    _seed_build(client, plugin_id="beta", version="1.0.0", runtime_type="docker")
    _seed_build(client, plugin_id="alpha", version="2.0.0", runtime_type="docker")
    _seed_build(client, plugin_id="alpha", version="1.0.0", runtime_type="docker")
    _seed_build(client, plugin_id="beta", version="1.0.0", runtime_type="conda-pack")

    response = client.get("/api/v1/registry/plugins", headers=headers)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(item["plugin_id"], item["version"], item["runtime_type"]) for item in items] == [
        ("alpha", "1.0.0", "docker"),
        ("alpha", "2.0.0", "docker"),
        ("beta", "1.0.0", "conda-pack"),
        ("beta", "1.0.0", "docker"),
    ]
    first = items[0]
    assert set(first) == {
        "plugin_id",
        "plugin_name",
        "version",
        "build_key",
        "runtime_type",
        "target_arch",
        "status",
        "package_sha256",
    }
    assert first["plugin_name"] == "alpha name"
    assert first["status"] == "ENABLED"
    assert first["target_arch"] == "amd64"


def test_registry_download_rejects_viewer_and_operator(client: TestClient) -> None:
    admin_headers = _admin_headers(client)
    build_key = _seed_build(
        client, plugin_id="nc_to_shp", version="1.0.0", runtime_type="conda-pack"
    )
    viewer_headers = _user_headers(client, admin_headers, "viewer")
    operator_headers = _user_headers(client, admin_headers, "operator")

    viewer_response = client.get(
        f"/api/v1/registry/download/{build_key}", headers=viewer_headers
    )
    operator_response = client.get(
        f"/api/v1/registry/download/{build_key}", headers=operator_headers
    )

    assert viewer_response.status_code == 403
    assert operator_response.status_code == 403


def test_registry_download_streams_exact_package_bytes(client: TestClient) -> None:
    admin_headers = _admin_headers(client)
    publisher_headers = _user_headers(client, admin_headers, "publisher")
    build_key = _seed_build(
        client, plugin_id="nc_to_shp", version="1.0.0", runtime_type="conda-pack"
    )

    response = client.get(
        f"/api/v1/registry/download/{build_key}", headers=publisher_headers
    )

    assert response.status_code == 200
    assert response.content == b"package:nc_to_shp:1.0.0:conda-pack"
    assert "nc_to_shp-1.0.0-conda-pack.pypkg" in response.headers["content-disposition"]


def test_registry_download_unknown_build_is_not_found(client: TestClient) -> None:
    headers = _admin_headers(client)

    response = client.get("/api/v1/registry/download/plugin_build_missing", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_FOUND"


def test_registry_download_without_stored_package_is_not_found(client: TestClient) -> None:
    headers = _admin_headers(client)
    build_key = _seed_build(
        client,
        plugin_id="nc_to_shp",
        version="1.0.0",
        runtime_type="docker",
        with_package=False,
    )

    response = client.get(f"/api/v1/registry/download/{build_key}", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLUGIN_BUILD_NOT_FOUND"


def test_registry_download_is_audited(client: TestClient) -> None:
    admin_headers = _admin_headers(client)
    publisher_headers = _user_headers(client, admin_headers, "publisher")
    build_key = _seed_build(client, plugin_id="nc_to_shp", version="1.0.0", runtime_type="docker")

    downloaded = client.get(
        f"/api/v1/registry/download/{build_key}", headers=publisher_headers
    )
    assert downloaded.status_code == 200
    logs = client.get(
        "/api/v1/audit-logs", headers=admin_headers, params={"action": "registry.download"}
    ).json()["items"]

    assert logs, "registry.download must be audited"
    entry = next(item for item in logs if item["resource_id"] == build_key)
    assert entry["resource_type"] == "plugin_build"
    assert entry["result"] == "ok"
    assert entry["actor_name"] == "publisher-user"


def test_files_by_sha256_rejects_invalid_hash(client: TestClient) -> None:
    headers = _admin_headers(client)

    for invalid in ("nothexatall", "z" * 64, "abc"):
        response = client.get(f"/api/v1/files/by-sha256/{invalid}", headers=headers)
        assert response.status_code == 422, invalid


def test_files_by_sha256_unknown_hash_is_not_found(client: TestClient) -> None:
    headers = _admin_headers(client)

    response = client.get(f"/api/v1/files/by-sha256/{'e' * 64}", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_files_by_sha256_returns_matching_upload(client: TestClient) -> None:
    headers = _admin_headers(client)
    payload = b"registry-dedup-payload"
    created = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("dedup.bin", payload, "application/octet-stream")},
    )
    assert created.status_code == 201
    file_id = created.json()["file_id"]
    sha256 = hashlib.sha256(payload).hexdigest()

    hit = client.get(f"/api/v1/files/by-sha256/{sha256}", headers=headers)

    assert hit.status_code == 200
    assert hit.json()["file_id"] == file_id
    assert hit.json()["sha256"] == sha256


def test_files_by_sha256_requires_operator_role(client: TestClient) -> None:
    admin_headers = _admin_headers(client)
    viewer_headers = _user_headers(client, admin_headers, "viewer")

    response = client.get(f"/api/v1/files/by-sha256/{'e' * 64}", headers=viewer_headers)

    assert response.status_code == 403
