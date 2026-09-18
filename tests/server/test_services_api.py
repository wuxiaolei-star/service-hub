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


class ManagerWithMissingContainer(FakeManager):
    """Manager that reports one named service as having no container.

    This is the state a real host lands in whenever a deploy is refused (the
    definition is still saved, as STOPPED) or a container is removed out of band.
    """

    def __init__(self, missing: set[str]) -> None:
        super().__init__()
        self.missing = missing

    def status(self, name: str) -> dict[str, object]:
        self.calls.append(("status", {"name": name}))
        if name in self.missing:
            from hub_server.errors import HubError

            raise HubError(
                code="SERVICE_CONTAINER_NOT_FOUND", message="服务容器不存在", status_code=404
            )
        return {"name": f"hub-svc-{name}", "state": "running", "health": "healthy"}


def _settings(tmp_path: Path) -> HubSettings:
    return HubSettings(
        deployment=DeploymentSettings(mode="offline"),
        storage=StorageSettings(root=tmp_path / "data"),
        database=DatabaseSettings(url=f"sqlite:///{(tmp_path / 'hub.db').as_posix()}"),
        uploads=UploadSettings(max_size_bytes=1024),
        runner=RunnerSettings(shared_token="runner-test-secret", poll_interval_seconds=1),
        auth=AuthSettings(mode="required"),
    )


HOST_DATA_ROOT = "/srv/hub-data"


def _client(tmp_path: Path, manager: FakeManager, monkeypatch: Any) -> TestClient:
    import hub_server.routers.services as services

    monkeypatch.setenv("HUB_DOCKER_HOST_DATA_ROOT", HOST_DATA_ROOT)
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
        "mounts": [
            {"source": f"{HOST_DATA_ROOT}/hello", "target": "/data", "read_only": True}
        ],
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


def test_list_survives_a_definition_whose_container_is_gone(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """One containerless definition must not blank the whole catalogue.

    The list used to forward the manager's 404 straight out, so a single leftover
    definition (exactly what a refused deploy leaves behind) made GET /services
    answer 404 forever - and the 404 in turn made later creates look like
    SERVICE_NAME_TAKEN. Only a real host reaches this state, because the unit
    fake never refuses a status call.
    """
    manager = ManagerWithMissingContainer(missing={"ghost"})
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)

        with client.app.state.session_factory() as session:
            session.add(
                ServiceDef(
                    name="ghost",
                    image="ghost:1.0",
                    container_name="hub-svc-ghost",
                    ports_json=[{"host": 18099, "container": 80}],
                    env_json={},
                    mounts_json=[],
                    command_json=None,
                    user_label="65532:65532",
                    desired_state="STOPPED",
                )
            )
            session.commit()

        response = client.get("/api/v1/services", headers=admin)

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert [item["name"] for item in items] == ["ghost"]
        # The entry is still reported, just with an unknown runtime.
        assert items[0]["runtime"]["state"] == "unknown"
        assert items[0]["desired_state"] == "STOPPED"


def test_list_reflects_a_container_the_manager_reports_normally(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The isolation must not swallow a healthy manager reply."""
    manager = ManagerWithMissingContainer(missing=set())
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        created = client.post("/api/v1/services", headers=admin, json=_payload())
        assert created.status_code == 201, created.text

        response = client.get("/api/v1/services", headers=admin)

        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert items[0]["name"] == "hello"
        assert items[0]["runtime"]["state"] == "running"
        assert items[0]["runtime"]["health"] == "healthy"


def test_create_rejects_mounts_outside_the_host_data_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A service must never be able to bind-mount arbitrary host paths."""
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        response = client.post(
            "/api/v1/services",
            headers=_admin_headers(client),
            json=_payload(
                mounts=[{"source": "/var/run/docker.sock", "target": "/sock"}],
            ),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SERVICE_MOUNT_FORBIDDEN"
        assert manager.calls == []


def test_create_rejects_reserved_and_privileged_host_ports(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        for host_port in (80, 8000, 8001, 8080):
            response = client.post(
                "/api/v1/services",
                headers=admin,
                json=_payload(ports=[{"host": host_port, "container": 80}]),
            )
            assert response.status_code == 422, host_port
        assert manager.calls == []


def test_create_rejects_root_user_labels(tmp_path: Path, monkeypatch: Any) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        for user_label in ("0:0", "0:1000", "root:root"):
            response = client.post(
                "/api/v1/services",
                headers=admin,
                json=_payload(user_label=user_label),
            )
            assert response.status_code == 422, user_label
        assert manager.calls == []


def test_update_rejects_mounts_outside_the_host_data_root(
    tmp_path: Path, monkeypatch: Any
) -> None:
    manager = FakeManager()
    with _client(tmp_path, manager, monkeypatch) as client:
        admin = _admin_headers(client)
        assert client.post("/api/v1/services", headers=admin, json=_payload()).status_code == 201

        response = client.put(
            "/api/v1/services/hello",
            headers=admin,
            json=_payload(mounts=[{"source": "/etc", "target": "/etc", "read_only": True}]),
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SERVICE_MOUNT_FORBIDDEN"
