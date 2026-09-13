"""Schedules endpoint contract tests (operator+ writes, admin-only delete)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, Schedule, UserRecord
from hub_server.routers.schedules import router as schedules_router
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


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_settings(tmp_path))
    app.include_router(schedules_router, prefix="/api/v1")
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


def _payload(name: str = "nightly-nc", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "plugin_id": "nc_to_shp",
        "version": "1.0.0",
        "runtime_type": "docker",
        "inputs": {},
        "params": {"group_name": "1"},
        "interval_minutes": 60,
    }
    body.update(overrides)
    return body


def test_create_returns_full_row_with_defaults(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        before = datetime.now(UTC)

        response = client.post("/api/v1/schedules", headers=headers, json=_payload())

        assert response.status_code == 201, response.text
        row = response.json()
        assert row["id"] > 0
        assert row["name"] == "nightly-nc"
        assert row["plugin_id"] == "nc_to_shp"
        assert row["version"] == "1.0.0"
        assert row["runtime_type"] == "docker"
        assert row["inputs"] == {}
        assert row["params"] == {"group_name": "1"}
        assert row["interval_minutes"] == 60
        assert row["enabled"] is True
        assert row["last_job_id"] is None
        created_at = datetime.fromisoformat(row["created_at"])
        next_run_at = datetime.fromisoformat(row["next_run_at"])
        assert created_at.tzinfo is not None
        assert created_at >= before - timedelta(seconds=5)
        assert timedelta(minutes=59) < next_run_at - created_at < timedelta(minutes=61)


def test_create_persists_schedule_row(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        created = client.post("/api/v1/schedules", headers=headers, json=_payload())
        assert created.status_code == 201

        with client.app.state.session_factory() as session:
            row = session.query(Schedule).filter(Schedule.name == "nightly-nc").one()
            assert row.plugin_id == "nc_to_shp"
            assert row.enabled is True
            assert row.last_job_id is None
            assert row.inputs_json == {}
            assert row.params_json == {"group_name": "1"}


def test_create_rejects_duplicate_name_with_409(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        first = client.post("/api/v1/schedules", headers=headers, json=_payload())
        assert first.status_code == 201

        second = client.post("/api/v1/schedules", headers=headers, json=_payload())

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "SCHEDULE_NAME_TAKEN"


def test_create_validates_request_body(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        for payload in (
            _payload(name="x" * 129),
            _payload(name="bad-interval", interval_minutes=0),
            _payload(name="bad-runtime", runtime_type="systemd"),
        ):
            response = client.post("/api/v1/schedules", headers=headers, json=payload)
            assert response.status_code == 422, payload


def test_list_returns_schedules_ordered_by_id(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        first = client.post("/api/v1/schedules", headers=headers, json=_payload("alpha"))
        second = client.post("/api/v1/schedules", headers=headers, json=_payload("beta"))
        assert first.status_code == 201
        assert second.status_code == 201

        response = client.get("/api/v1/schedules", headers=headers)

        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["name"] for item in items] == ["alpha", "beta"]
        assert [item["id"] for item in items] == sorted(item["id"] for item in items)


def test_enable_and_disable_toggle_enabled(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["operator"]
        schedule_id = client.post(
            "/api/v1/schedules", headers=headers, json=_payload()
        ).json()["id"]

        disabled = client.post(f"/api/v1/schedules/{schedule_id}/disable", headers=headers)
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False

        enabled = client.post(f"/api/v1/schedules/{schedule_id}/enable", headers=headers)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["enabled"] is True


def test_enable_disable_delete_missing_schedule_returns_404(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]
        for path in (
            "/api/v1/schedules/9999/enable",
            "/api/v1/schedules/9999/disable",
        ):
            response = client.post(path, headers=headers)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "SCHEDULE_NOT_FOUND"
        response = client.delete("/api/v1/schedules/9999", headers=admin)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SCHEDULE_NOT_FOUND"


def test_delete_removes_schedule(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]
        schedule_id = client.post(
            "/api/v1/schedules", headers=headers, json=_payload()
        ).json()["id"]

        deleted = client.delete(f"/api/v1/schedules/{schedule_id}", headers=admin)

        assert deleted.status_code == 200, deleted.text
        remaining = client.get("/api/v1/schedules", headers=admin)
        assert remaining.json()["items"] == []


def test_audit_records_schedule_actions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        headers = _seed_role_users(client, admin)["operator"]
        schedule_id = client.post(
            "/api/v1/schedules", headers=headers, json=_payload()
        ).json()["id"]
        client.post(f"/api/v1/schedules/{schedule_id}/disable", headers=headers)
        client.post(f"/api/v1/schedules/{schedule_id}/enable", headers=headers)
        client.delete(f"/api/v1/schedules/{schedule_id}", headers=admin)

        with client.app.state.session_factory() as session:
            actions = {
                entry.action
                for entry in session.query(AuditLogRecord)
                .filter(AuditLogRecord.action.like("schedule.%"))
                .all()
            }
        assert {
            "schedule.create",
            "schedule.disable",
            "schedule.enable",
            "schedule.delete",
        } <= actions


def test_anonymous_requests_are_rejected(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post("/api/v1/schedules", json=_payload())
        assert created.status_code == 401
        listed = client.get("/api/v1/schedules")
        assert listed.status_code == 401


def test_viewer_cannot_create_or_list(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        headers = _seed_role_users(client, _admin_headers(client))["viewer"]
        created = client.post("/api/v1/schedules", headers=headers, json=_payload())
        assert created.status_code == 403
        listed = client.get("/api/v1/schedules", headers=headers)
        assert listed.status_code == 403


def test_operator_can_enable_disable_but_not_delete(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        operator = _seed_role_users(client, admin)["operator"]
        schedule_id = client.post(
            "/api/v1/schedules", headers=operator, json=_payload()
        ).json()["id"]

        assert client.post(
            f"/api/v1/schedules/{schedule_id}/disable", headers=operator
        ).status_code == 200
        assert client.post(
            f"/api/v1/schedules/{schedule_id}/enable", headers=operator
        ).status_code == 200

        denied = client.delete(f"/api/v1/schedules/{schedule_id}", headers=operator)
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "FORBIDDEN"

        assert client.delete(
            f"/api/v1/schedules/{schedule_id}", headers=admin
        ).status_code == 200


def test_audit_entries_use_schedule_resource_type(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        admin = _admin_headers(client)
        operator = _seed_role_users(client, admin)["operator"]
        schedule_id = client.post(
            "/api/v1/schedules", headers=operator, json=_payload()
        ).json()["id"]

        entries = client.get(
            "/api/v1/audit-logs",
            params={"action": "schedule.create"},
            headers=admin,
        ).json()["items"]

        assert len(entries) == 1
        assert entries[0]["resource_type"] == "schedule"
        assert entries[0]["resource_id"] == str(schedule_id)
        assert entries[0]["result"] == "ok"
