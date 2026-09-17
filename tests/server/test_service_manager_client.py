"""HTTP mapping tests for the loopback service-manager client.

The services API tests replace ``get_service_manager`` with a fake, so nothing in
the suite ever exercised how this client turns a manager response into a HubError.
That blind spot is why a refused deploy surfaced as "manager unavailable".
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest
from hub_server.errors import HubError
from hub_server.services.service_manager import ServiceManagerClient

Handler = Callable[[httpx.Request], httpx.Response]


def _client(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> ServiceManagerClient:
    """Build a client whose internal httpx.Client answers via a mock transport."""
    original = httpx.Client

    def factory(**kwargs: object) -> httpx.Client:
        return original(
            transport=httpx.MockTransport(handler),
            base_url=str(kwargs.get("base_url", "http://manager.test")),
        )

    monkeypatch.setattr(httpx, "Client", factory)
    return ServiceManagerClient(base_url="http://manager.test", token="runner-test-secret")


def test_validation_rejection_keeps_the_reason_the_manager_gave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "SERVICE_IMAGE_MISSING"})

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).deploy({"name": "web"})

    assert raised.value.code == "SERVICE_IMAGE_MISSING"
    assert raised.value.status_code == 422
    assert "SERVICE_IMAGE_MISSING" in raised.value.message


@pytest.mark.parametrize(
    "detail",
    ["SERVICE_USER_FORBIDDEN", "SERVICE_PORT_FORBIDDEN", "SERVICE_MOUNT_FORBIDDEN"],
)
def test_every_boundary_rejection_is_relayed(
    monkeypatch: pytest.MonkeyPatch, detail: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": detail})

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).deploy({"name": "web"})

    assert raised.value.code == detail
    assert raised.value.status_code == 422


def test_unrecognised_4xx_still_reads_as_a_gateway_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": ["not", "a", "string"]})

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).deploy({"name": "web"})

    assert raised.value.code == "SERVICE_MANAGER_REJECTED"
    assert raised.value.status_code == 502


def test_missing_container_maps_to_404(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "SERVICE_NOT_FOUND"})

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).status("web")

    assert raised.value.code == "SERVICE_CONTAINER_NOT_FOUND"
    assert raised.value.status_code == 404


def test_manager_fault_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).status("web")

    assert raised.value.code == "SERVICE_MANAGER_UNAVAILABLE"
    assert raised.value.status_code == 502


def test_transport_failure_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).status("web")

    assert raised.value.code == "SERVICE_MANAGER_UNAVAILABLE"
    assert raised.value.status_code == 502


def test_non_object_body_maps_to_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"[1, 2, 3]", headers={"content-type": "application/json"}
        )

    with pytest.raises(HubError) as raised:
        _client(monkeypatch, handler).status("web")

    assert raised.value.code == "SERVICE_MANAGER_UNAVAILABLE"


def test_successful_body_is_returned_as_a_string_keyed_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"name": "hub-svc-web", "state": "running", "health": None})

    body = _client(monkeypatch, handler).status("web")

    assert body == {"name": "hub-svc-web", "state": "running", "health": None}
