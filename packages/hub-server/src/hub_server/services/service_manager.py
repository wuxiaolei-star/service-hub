"""Typed loopback client for the isolated service-manager process."""

from __future__ import annotations

import os

import httpx

from hub_server.errors import HubError
from hub_server.settings import HubSettings

_DEFAULT_MANAGER_URL = "http://127.0.0.1:8001"


class ServiceManagerClient:
    """Forward Hub-approved service operations to the local manager only."""

    def __init__(self, *, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def deploy(self, payload: dict[str, object]) -> dict[str, object]:
        return self._request("POST", "/deploy", json=payload)

    def status(self, name: str) -> dict[str, object]:
        return self._request("GET", f"/{name}/status")

    def logs(self, name: str, tail: int) -> dict[str, object]:
        return self._request("GET", f"/{name}/logs", params={"tail": tail})

    def action(self, name: str, action: str) -> dict[str, object]:
        return self._request("POST", f"/{name}/{action}")

    def remove(self, name: str) -> dict[str, object]:
        return self._request("DELETE", f"/{name}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, object]:
        try:
            with httpx.Client(base_url=self._base_url, timeout=5.0) as client:
                response = client.request(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json=json,
                    params=params,
                )
        except httpx.HTTPError as exc:
            raise _manager_unavailable() from exc

        if response.status_code == 404:
            raise HubError(
                code="SERVICE_CONTAINER_NOT_FOUND",
                message="服务容器不存在",
                status_code=404,
            )
        if response.status_code >= 500:
            raise _manager_unavailable()
        if response.status_code >= 400:
            raise HubError(
                code="SERVICE_MANAGER_REJECTED",
                message="服务管理器拒绝请求",
                status_code=502,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise _manager_unavailable() from exc
        if not isinstance(body, dict):
            raise _manager_unavailable()
        return {str(key): value for key, value in body.items()}


def get_service_manager(settings: HubSettings) -> ServiceManagerClient:
    """Create a manager client using the runner token injected at deployment."""
    return ServiceManagerClient(
        base_url=os.environ.get("HUB_SERVICE_MANAGER_URL", _DEFAULT_MANAGER_URL),
        token=settings.runner.shared_token.get_secret_value(),
    )


def _manager_unavailable() -> HubError:
    return HubError(
        code="SERVICE_MANAGER_UNAVAILABLE",
        message="服务管理器不可用",
        status_code=502,
    )
