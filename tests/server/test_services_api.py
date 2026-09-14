"""Long-running service API contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from hub_server.main import create_app
from hub_server.models import AuditLogRecord, ServiceDef, UserRecord
from hub_server.services.auth import AuthService
from hub_server.settings import (
    AuthSettings,
    DatabaseSettings,
    DeploymentSettings,
    HubSettings,
    RunnerSettings,
    StorageSettings,
    UploadSettings,
)


class FakeManager:
    """In-memory manager double used to verify Hub request forwarding."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def deploy(self, payload: dict[str, Any]) -> dict[str, object]:
        self.calls.append(("deploy", payload))
        self._raise_if_unavailable()
        return {"name": f"hub-svc-{payload['name']}", "state": "running"}

    def action(self, name: str, action: str) -> dict[str, object]:
        self.calls.append((action, {"name": name}))
        self._raise_if_unavailable()
        return {"name": f"hub-svc-{name}", "state": "running"}

    def remove(self, name: str) -> dict[str, object]:
        self.calls.append(("remove", {"name": name}))
        self._raise_if_unavailable()
        return {"name": f"hub-svc-{name}", "state": "REMOVED"}

    def logs(self, name: str, tail: int) -> dict[str, object]:
        self.calls.append(("logs", {"name": name, "tail": tail}))
        self._raise_if_unavailable()
        return {"name": f"hub-svc-{name}", "logs": "service ready\n"}

    def status(self, name: str) -> dict[str, object]:
        self.calls.append(("status", {"name": name}))
        self._raise_if_unavailable()
        return {"name": f"hub-svc-{name}", "state": "running", "health": "healthy"}

    def _raise_if_unavailable(self) -> None:
        if self.unavailable:
            from hub_server.errors import HubError

            raise HubError(
                code="SERVICE_MANAGER_UNAVAILABLE", message="服务管理器不可用", status_code=502
            )


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),
    )


def _client(tmp_path: Path, manager: FakeManager, monkeypatch: Any) -> TestClient:
    import hub_server.routers.services as services

    monkeypatch.setattr(services, "get_service_manager", lambda _settings: manager)
    return TestClient(create_app(_settings(tmp_path)))


def _headers_for(client: TestClient, username: str) -> dict[str, str]:
    with client.app.state.session_factory() as session:
        user = session.query(UserRecord).filter(UserRecord.username == username).one()
        _record, token = AuthService(session).create_session(user.id)
        session.commit()
    return {"Authorization": f"Bearer {token}"}


def _admin_headers(client: TestClient) -> dict[str, str]:
    return _headers_for(client, "admin")


def _create_user(
    client: TestClient, admin: dict[str, str], username: str, role: str
) -> dict[str, str]:
    response = client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": username, "password": "long-enough-pass", "role": role},
    )
    assert response.status_code == 201, response.text
    return _headers_for(client, username)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "hello",
        "image": "nginxdemos/hello:plain-text",
        "ports": [{"host": 18081, "container": 80}],
        "env": {"MODE": "test"},
        "mounts": [{"source": "/srv/hub", "target": "/data", "read_only": True}],
        "command": ["nginx", "-g", "daemon off;"],
    }
    payload.update(overrides)
    return payload


def test_admin_creates_service_persists_running_state_and_audits(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        response = client.post("/api/v1/services", headers=_admin_headers(client), json=_payload())

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "hello"
        assert body["container_name"] == "hub-svc-hello"
        assert body["runtime"]["state"] == "running"
        assert body["desired_state"] == "RUNNING"
        assert "env" not in body
        assert "mounts" not in body
        assert manager.calls[0][0] == "deploy"
        assert manager.calls[0][1]["ports"] == [{"host": 18081, "container": 80}]
        with client.app.state.session_factory() as session:
            definition = session.query(ServiceDef).filter_by(name="hello").one()
            assert definition.desired_state == "RUNNING"
            entries = session.query(AuditLogRecord).filter_by(action="service.create").all()
            assert len(entries) == 1


def test_viewer_can_read_but_cannot_mutate(tmp_path: Path, monkeypatch: Any) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        viewer = _create_user(client, admin, "spectator", "viewer")

        assert client.get("/api/v1/services", headers=viewer).status_code == 200
        for method, path in (
            ("post", "/api/v1/services"),
            ("put", "/api/v1/services/hello"),
            ("post", "/api/v1/services/hello/start"),
        ):
            response = getattr(client, method)(path, headers=viewer, json=_payload())
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"
        deleted = client.delete("/api/v1/services/hello", headers=viewer)
        assert deleted.status_code == 403
        assert deleted.json()["error"]["code"] == "FORBIDDEN"


def test_manager_unavailable_returns_unified_502_and_does_not_mark_running(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = FakeManager(unavailable=True)
    with _client(tmp_path, manager, monkeypatch) as client:
        response = client.post("/api/v1/services", headers=_admin_headers(client), json=_payload())

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "SERVICE_MANAGER_UNAVAILABLE"
        with client.app.state.session_factory() as session:
            definition = session.query(ServiceDef).filter_by(name="hello").one()
            assert definition.desired_state == "STOPPED"


def test_logs_forwards_bounded_tail(tmp_path: Path, monkeypatch: Any) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        assert client.post("/api/v1/services", headers=admin, json=_payload()).status_code == 201

        response = client.get("/api/v1/services/hello/logs", headers=admin, params={"tail": 42})

        assert response.status_code == 200
        assert response.text == "service ready\n"
        assert ("logs", {"name": "hello", "tail": 42}) in manager.calls
