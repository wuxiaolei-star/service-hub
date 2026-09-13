"""Pipelines endpoint contract tests (viewer reads, operator writes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import (
    AuditLogRecord,
    Environment,
    Job,
    Pipeline,
    PipelineRun,
    Plugin,
    PluginBuild,
    PluginVersion,
    UserRecord,
)
from hub_server.routers.pipelines import router as pipelines_router
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
from sqlalchemy.orm import Session


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


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(pipelines_router, prefix="/api/v1")
    return TestClient(app)


def _headers_for(client: TestClient, username: str) -> dict[str, str]:
    factory = client.app.state.session_factory
    with factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(client: TestClient) -> dict[str, str]:
    return _headers_for(client, "admin")


def _seed_role_users(client: TestClient, admin: dict[str, str]) -> dict[str, dict[str, str]]:
    for username, role in (("ops", "operator"), ("spectator", "viewer")):
        created = client.post(
            "/api/v1/users",
            headers=admin,
            json={"username": username, "password": "long-enough-pass", "role": role},
        )
        assert created.status_code == 201, created.text
    return {
        "operator": _headers_for(client, "ops"),
        "viewer": _headers_for(client, "spectator"),
    }


def _step(**overrides: object) -> dict[str, object]:
    step: dict[str, object] = {
        "plugin_id": "nc_to_shp",
        "version": "1.0.0",
        "runtime_type": "docker",
        "inputs": {},
        "params": {},
    }
    step.update(overrides)
    return step


def _payload(
    name: str = "nc-chain", steps: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {"name": name, "steps": steps if steps is not None else [_step()]}


def test_create_returns_full_row(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        before = datetime.now(UTC)

        response = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json=_payload(steps=[_step(inputs={"source_nc": "file_x"})]),
        )

        assert response.status_code == 201, response.text
        row = response.json()
        assert row["id"] > 0
        assert row["name"] == "nc-chain"
        assert row["steps"] == [_step(inputs={"source_nc": "file_x"})]
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        assert created_at.tzinfo is not None
        assert created_at >= before - timedelta(seconds=5)
        assert updated_at >= created_at


def test_create_persists_steps_json(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        created = client.post("/api/v1/pipelines", headers=headers, json=_payload())
        assert created.status_code == 201

        with client.app.state.session_factory() as session:
            row = session.query(Pipeline).filter(Pipeline.name == "nc-chain").one()
            assert row.steps_json == [_step()]


def test_create_rejects_empty_steps(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]

        response = client.post("/api/v1/pipelines", headers=headers, json=_payload(steps=[]))

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PIPELINE_INVALID"


def test_create_rejects_first_step_prev_reference(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]

        response = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json=_payload(steps=[_step(inputs={"source_nc": "$prev.result"})]),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PIPELINE_INVALID"


def test_create_rejects_invalid_step_fields(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        for steps in (
            [_step(plugin_id="")],
            [{}],
            [_step(runtime_type="systemd")],
            [_step(inputs=["not-a-dict"])],
            [_step(params="nope")],
        ):
            response = client.post("/api/v1/pipelines", headers=headers, json=_payload(steps=steps))
            assert response.status_code == 422, steps
            assert response.json()["error"]["code"] == "PIPELINE_INVALID", steps


def test_create_rejects_duplicate_name_with_409(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        first = client.post("/api/v1/pipelines", headers=headers, json=_payload())
        assert first.status_code == 201

        second = client.post("/api/v1/pipelines", headers=headers, json=_payload())

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "PIPELINE_NAME_TAKEN"


def test_list_returns_pipelines_ordered_by_id(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        first = client.post("/api/v1/pipelines", headers=headers, json=_payload("alpha"))
        second = client.post("/api/v1/pipelines", headers=headers, json=_payload("beta"))
        assert first.status_code == 201
        assert second.status_code == 201

        response = client.get("/api/v1/pipelines", headers=headers)

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["name"] for item in items] == ["alpha", "beta"]


def test_get_pipeline_returns_row(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        created = client.post("/api/v1/pipelines", headers=headers, json=_payload()).json()

        response = client.get(f"/api/v1/pipelines/{created['id']}", headers=headers)

        assert response.status_code == 200
        assert response.json()["name"] == "nc-chain"


def test_get_missing_pipeline_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]

        response = client.get("/api/v1/pipelines/9999", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PIPELINE_NOT_FOUND"


def test_anonymous_requests_are_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        listed = client.get("/api/v1/pipelines")
        assert listed.status_code == 401
        created = client.post("/api/v1/pipelines", json=_payload())
        assert created.status_code == 401
        executed = client.post("/api/v1/pipelines/1/execute")
        assert executed.status_code == 401


def test_viewer_can_read_but_not_write(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["viewer"]

        listed = client.get("/api/v1/pipelines", headers=headers)
        assert listed.status_code == 200

        created = client.post("/api/v1/pipelines", headers=headers, json=_payload())
        assert created.status_code == 403
        assert created.json()["error"]["code"] == "FORBIDDEN"

        executed = client.post("/api/v1/pipelines/9999/execute", headers=headers)
        assert executed.status_code == 403
        assert executed.json()["error"]["code"] == "FORBIDDEN"


def test_execute_creates_run_and_first_step_job(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]
        with client.app.state.session_factory() as session:
            _seed_build(session)
        file_id = _upload_nc(client, admin)
        created = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json=_payload(steps=[_step(inputs={"source_nc": file_id})]),
        )
        assert created.status_code == 201
        pipeline_id = created.json()["id"]

        response = client.post(f"/api/v1/pipelines/{pipeline_id}/execute", headers=headers)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == "RUNNING"
        assert body["run_id"] > 0
        assert body["job_id"].startswith("job_")
        with client.app.state.session_factory() as session:
            run = session.query(PipelineRun).filter_by(id=body["run_id"]).one()
            assert run.pipeline_id == pipeline_id
            assert run.state == "RUNNING"
            assert run.current_step == 0
            job = session.query(Job).filter(Job.job_key == body["job_id"]).one()
            assert job.pipeline_run_id == run.id
            actions = {entry.action for entry in session.query(AuditLogRecord).all()}
        assert {"pipeline.create", "pipeline.execute", "pipeline.step"} <= actions


def test_execute_missing_pipeline_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]

        response = client.post("/api/v1/pipelines/9999/execute", headers=headers)

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "PIPELINE_NOT_FOUND"


def test_runs_listing_orders_and_filters(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]
        with client.app.state.session_factory() as session:
            _seed_build(session)
        file_id = _upload_nc(client, admin)
        first = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json=_payload("alpha", steps=[_step(inputs={"source_nc": file_id})]),
        ).json()["id"]
        second = client.post(
            "/api/v1/pipelines",
            headers=headers,
            json=_payload("beta", steps=[_step(inputs={"source_nc": file_id})]),
        ).json()["id"]
        for executed in (
            client.post(f"/api/v1/pipelines/{first}/execute", headers=headers),
            client.post(f"/api/v1/pipelines/{first}/execute", headers=headers),
            client.post(f"/api/v1/pipelines/{second}/execute", headers=headers),
        ):
            assert executed.status_code == 201

        listed = client.get("/api/v1/pipelines/runs", headers=headers)
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert len(items) == 3
        assert [item["id"] for item in items] == sorted(
            (item["id"] for item in items), reverse=True
        )
        assert set(items[0]) == {"id", "pipeline_id", "state", "current_step", "created_at"}
        assert items[0]["state"] == "RUNNING"
        assert items[0]["current_step"] == 0

        filtered = client.get(
            "/api/v1/pipelines/runs", params={"pipeline_id": second}, headers=headers
        )
        assert filtered.status_code == 200
        filtered_items = filtered.json()["items"]
        assert len(filtered_items) == 1
        assert filtered_items[0]["pipeline_id"] == second


def _upload_nc(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/files",
        headers=headers,
        files={"file": ("sample.nc", b"netcdf", "application/x-netcdf")},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["file_id"])


def _seed_build(session: Session, *, runtime_type: str = "docker") -> None:
    """Seed one enabled nc_to_shp docker build with its ready environment."""
    plugin = Plugin(plugin_key="nc_to_shp", name="NC to Shapefile")
    version = PluginVersion(
        plugin=plugin,
        version="1.0.0",
        spec_version="1.0",
        sdk_version="1.0",
        source_sha256="a" * 64,
        manifest_json=_manifest(),
        status="INSTALLED",
    )
    runtime_metadata = (
        {"type": "conda-pack", "archive": "runtime/env.tar.zst", "fingerprint": "c" * 64}
        if runtime_type == "conda-pack"
        else {"type": "docker", "archive": "image.tar.zst", "image": "nc", "digest": "d" * 64}
    )
    fingerprint = runtime_metadata.get("fingerprint") or runtime_metadata["digest"]
    build = PluginBuild(
        build_key=f"plugin_build_{runtime_type.replace('-', '_')}",
        manifest_build_id=f"build-{runtime_type}",
        plugin_version=version,
        target_os="linux",
        target_arch="amd64",
        runtime_type=runtime_type,
        python_version="3.12",
        sdk_version="1.0",
        package_path=f"plugins/plugin_build_{runtime_type}",
        package_sha256="b" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/env.tar.zst",
        runtime_fingerprint=str(fingerprint),
        build_metadata_json={
            "schema_version": "1.0",
            "build_id": f"build-{runtime_type}",
            "plugin_id": "nc_to_shp",
            "plugin_version": "1.0.0",
            "target": {"os": "linux", "arch": "amd64"},
            "python_version": "3.12",
            "runtime": runtime_metadata,
            "sdk_version": "1.0.0",
            "source_sha256": "a" * 64,
            "built_at": datetime.now(UTC).isoformat(),
        },
        status="ENABLED",
    )
    environment = Environment(
        plugin_build=build,
        runtime_type=runtime_type,
        fingerprint=str(fingerprint),
        environment_path=None,
        image_digest=str(fingerprint) if runtime_type == "docker" else None,
        metadata_json=runtime_metadata,
        status="READY",
    )
    session.add_all([plugin, version, build, environment])
    session.commit()


def _manifest() -> dict[str, object]:
    return {
        "spec_version": "1.0",
        "plugin": {"id": "nc_to_shp", "name": "NC to Shapefile", "version": "1.0.0"},
        "sdk": {"version": "1.0"},
        "runtime": {"type": "process", "python": {"version": "3.12"}},
        "entrypoint": {"module": "nc_to_shp_plugin.main", "function": "run"},
        "parameters": [],
        "inputs": [
            {
                "name": "source_nc",
                "label": "Source NC",
                "type": "file",
                "required": True,
                "extensions": [".nc"],
            }
        ],
        "outputs": [{"name": "result_files", "label": "Result", "type": "file", "required": True}],
        "execution": {"timeout": 30, "concurrency": 1},
        "environment_variables": {"required": []},
        "healthcheck": {"enabled": True, "type": "import"},
    }
