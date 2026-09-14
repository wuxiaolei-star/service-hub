"""Tests for the V3.0 service-manager Docker executor."""

from __future__ import annotations

from typing import Any

import pytest
from docker.errors import NotFound
from fastapi.testclient import TestClient

from deploy.service_hub import service_manager_service as service_manager

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeContainer:
    """Minimal docker-py Container double that records every call."""

    def __init__(
        self, name: str, *, status: str = "running", attrs: dict[str, Any] | None = None
    ) -> None:
        self.name = name
        self.status = status
        self.attrs: dict[str, Any] = attrs if attrs is not None else {"State": {"Status": status}}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, action: str, **kwargs: Any) -> None:
        self.calls.append((action, kwargs))

    def remove(self, **kwargs: Any) -> None:
        self._record("remove", **kwargs)

    def stop(self, **kwargs: Any) -> None:
        self._record("stop", **kwargs)
        self.status = "exited"

    def start(self, **kwargs: Any) -> None:
        self._record("start", **kwargs)
        self.status = "running"

    def restart(self, **kwargs: Any) -> None:
        self._record("restart", **kwargs)
        self.status = "running"

    def logs(self, **kwargs: Any) -> bytes:
        self._record("logs", **kwargs)
        return b"2026-09-14 boot ok\n2026-09-14 serving\n"


class FakeDockerClient:
    """Minimal docker-py client double exposing client.containers.get/run."""

    def __init__(self) -> None:
        self._by_name: dict[str, FakeContainer] = {}
        self.run_calls: list[dict[str, Any]] = []
        self.containers = SimpleNamespaceLike(self)

    def add(self, container: FakeContainer) -> None:
        self._by_name[container.name] = container

    def get(self, name: str) -> FakeContainer:
        try:
            return self._by_name[name]
        except KeyError:
            raise NotFound(f"container {name} not found") from None

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        self.run_calls.append({"image": image, **kwargs})
        container = FakeContainer(kwargs["name"])
        self._by_name[kwargs["name"]] = container
        return container


class SimpleNamespaceLike:
    """Stands in for the ``client.containers`` collection of docker-py."""

    def __init__(self, client: FakeDockerClient) -> None:
        self._client = client

    def get(self, name: str) -> FakeContainer:
        return self._client.get(name)

    def run(self, image: str, **kwargs: Any) -> FakeContainer:
        return self._client.run(image, **kwargs)


@pytest.fixture()
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeDockerClient:
    monkeypatch.setenv("HUB_RUNNER_TOKEN", TOKEN)
    fake = FakeDockerClient()
    monkeypatch.setattr(service_manager, "_docker_client", lambda: fake)
    return fake


@pytest.fixture()
def api(fake_client: FakeDockerClient) -> TestClient:
    return TestClient(service_manager.app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/ping"),
        ("post", "/deploy"),
        ("post", "/web/stop"),
        ("post", "/web/start"),
        ("delete", "/web"),
        ("get", "/web/status"),
        ("get", "/web/logs"),
    ],
)
def test_endpoints_reject_missing_token(api: TestClient, method: str, path: str) -> None:
    response = getattr(api, method)(path)
    assert response.status_code == 401


def test_endpoints_reject_wrong_token(api: TestClient) -> None:
    response = api.get("/ping", headers={"Authorization": "Bearer not-the-token"})
    assert response.status_code == 401


def test_missing_token_environment_rejects_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HUB_RUNNER_TOKEN", raising=False)
    monkeypatch.setattr(service_manager, "_docker_client", lambda: FakeDockerClient())
    client = TestClient(service_manager.app)
    assert client.get("/ping").status_code == 401
    assert client.post("/deploy", json={"name": "x", "image": "img:1"}).status_code == 401


def test_ping_ok(api: TestClient) -> None:
    response = api.get("/ping", headers=AUTH)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_deploy_converts_request_to_docker_py_format(
    api: TestClient, fake_client: FakeDockerClient
) -> None:
    response = api.post(
        "/deploy",
        headers=AUTH,
        json={
            "name": "web",
            "image": "ghcr.io/example/web:2.0",
            "ports": [{"host": 8081, "container": 80}],
            "env": {"HUB_ENV": "prod"},
            "mounts": [
                {"source": "/srv/hub/web", "target": "/data", "read_only": True},
                {"source": "/srv/cache", "target": "/cache", "read_only": False},
            ],
            "command": ["gunicorn", "-b", "0.0.0.0:80"],
            "user_label": "1000:1000",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "running"}
    assert len(fake_client.run_calls) == 1
    call = fake_client.run_calls[0]
    assert call["image"] == "ghcr.io/example/web:2.0"
    assert call["name"] == "hub-svc-web"
    assert call["detach"] is True
    assert call["ports"] == {"127.0.0.1:8081": 80}
    assert call["environment"] == {"HUB_ENV": "prod"}
    assert call["volumes"] == {
        "/srv/hub/web": {"bind": "/data", "mode": "ro"},
        "/srv/cache": {"bind": "/cache", "mode": "rw"},
    }
    assert call["command"] == ["gunicorn", "-b", "0.0.0.0:80"]
    assert call["user"] == "1000:1000"
    assert call["restart_policy"] == {"Name": "unless-stopped"}


def test_deploy_replaces_existing_container(
    api: TestClient, fake_client: FakeDockerClient
) -> None:
    existing = FakeContainer("hub-svc-web")
    fake_client.add(existing)

    response = api.post("/deploy", headers=AUTH, json={"name": "web", "image": "img:1"})

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "running"}
    assert existing.calls == [("remove", {"force": True})]
    assert [call["name"] for call in fake_client.run_calls] == ["hub-svc-web"]


def test_deploy_defaults_are_forwarded(api: TestClient, fake_client: FakeDockerClient) -> None:
    response = api.post("/deploy", headers=AUTH, json={"name": "worker", "image": "img:1"})

    assert response.status_code == 200
    call = fake_client.run_calls[0]
    assert call["name"] == "hub-svc-worker"
    assert call["ports"] == {}
    assert call["environment"] == {}
    assert call["volumes"] == {}
    assert call["command"] is None
    assert call["user"] == "65532:65532"


def test_stop_stops_container_and_reports_state(
    api: TestClient, fake_client: FakeDockerClient
) -> None:
    container = FakeContainer("hub-svc-web", status="running")
    fake_client.add(container)

    response = api.post("/web/stop", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "exited"}
    assert ("stop", {}) in container.calls


def test_start_starts_container(api: TestClient, fake_client: FakeDockerClient) -> None:
    container = FakeContainer("hub-svc-web", status="exited")
    fake_client.add(container)

    response = api.post("/web/start", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "running"}
    assert ("start", {}) in container.calls


def test_restart_restarts_container(api: TestClient, fake_client: FakeDockerClient) -> None:
    container = FakeContainer("hub-svc-web", status="running")
    fake_client.add(container)

    response = api.post("/web/restart", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "running"}
    assert ("restart", {}) in container.calls


def test_remove_forces_container_removal(
    api: TestClient, fake_client: FakeDockerClient
) -> None:
    container = FakeContainer("hub-svc-web", status="exited")
    fake_client.add(container)

    response = api.delete("/web", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"name": "hub-svc-web", "state": "REMOVED"}
    assert container.calls == [("remove", {"force": True})]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/ghost/stop"),
        ("post", "/ghost/start"),
        ("post", "/ghost/restart"),
        ("delete", "/ghost"),
        ("get", "/ghost/status"),
        ("get", "/ghost/logs"),
    ],
)
def test_unknown_container_returns_404(
    api: TestClient, method: str, path: str
) -> None:
    response = getattr(api, method)(path, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["detail"] == "SERVICE_NOT_FOUND"


STATUS_ATTRS = {
    "Created": "2026-09-14T10:00:00.123456789Z",
    "State": {"Status": "running", "Health": {"Status": "healthy", "FailingStreak": 0}},
    "NetworkSettings": {"Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]}},
    "Config": {"Image": "ghcr.io/example/web:2.0"},
}


def test_status_extracts_container_attrs(api: TestClient, fake_client: FakeDockerClient) -> None:
    fake_client.add(FakeContainer("hub-svc-web", attrs=STATUS_ATTRS))

    response = api.get("/web/status", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "name": "hub-svc-web",
        "state": "running",
        "health": "healthy",
        "ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]},
        "image": "ghcr.io/example/web:2.0",
        "created_at": "2026-09-14T10:00:00.123456789Z",
    }


def test_status_without_health_reports_null_health(
    api: TestClient, fake_client: FakeDockerClient
) -> None:
    attrs = {
        "Created": "2026-09-14T10:00:00Z",
        "State": {"Status": "exited"},
        "NetworkSettings": {"Ports": {}},
        "Config": {"Image": "img:1"},
    }
    fake_client.add(FakeContainer("hub-svc-worker", attrs=attrs))

    body = api.get("/worker/status", headers=AUTH).json()

    assert body["state"] == "exited"
    assert body["health"] is None
    assert body["ports"] == {}


def test_logs_decode_and_forward_tail(api: TestClient, fake_client: FakeDockerClient) -> None:
    container = FakeContainer("hub-svc-web")
    fake_client.add(container)

    response = api.get("/web/logs", params={"tail": 42}, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "hub-svc-web"
    assert body["logs"] == "2026-09-14 boot ok\n2026-09-14 serving\n"
    assert container.calls == [("logs", {"tail": 42, "timestamps": False})]


def test_logs_default_tail(api: TestClient, fake_client: FakeDockerClient) -> None:
    container = FakeContainer("hub-svc-web")
    fake_client.add(container)

    response = api.get("/web/logs", headers=AUTH)

    assert response.status_code == 200
    assert container.calls == [("logs", {"tail": 200, "timestamps": False})]
