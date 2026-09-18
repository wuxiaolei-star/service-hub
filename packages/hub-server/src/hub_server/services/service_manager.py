"""Typed loopback client for the isolated service-manager process."""

from __future__ import annotations

import os

import httpx

from hub_server.errors import HubError
from hub_server.settings import HubSettings

_DEFAULT_MANAGER_URL = "http://127.0.0.1:8001"

# Reads must fail fast: a wedged manager should not hold a request open.
_QUERY_TIMEOUT_SECONDS = 5.0
# A lifecycle action waits on the Docker daemon, which gives a container a
# 10-second grace period before SIGKILL. A 5-second socket timeout therefore
# expired before the daemon answered and the Hub reported 502
# SERVICE_MANAGER_UNAVAILABLE for a stop that actually succeeded. Allow room for
# the stop plus the manager's own round trip.
_MUTATION_TIMEOUT_SECONDS = 60.0


class ServiceManagerClient:
    """Forward Hub-approved service operations to the local manager only."""

    def __init__(self, *, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def deploy(self, payload: dict[str, object]) -> dict[str, object]:
        return self._request(
            "POST", "/deploy", json=payload, timeout=_MUTATION_TIMEOUT_SECONDS
        )

    def status(self, name: str) -> dict[str, object]:
        return self._request("GET", f"/{name}/status")

    def logs(self, name: str, tail: int) -> dict[str, object]:
        return self._request("GET", f"/{name}/logs", params={"tail": tail})

    def action(self, name: str, action: str) -> dict[str, object]:
        return self._request("POST", f"/{name}/{action}", timeout=_MUTATION_TIMEOUT_SECONDS)

    def remove(self, name: str) -> dict[str, object]:
        return self._request("DELETE", f"/{name}", timeout=_MUTATION_TIMEOUT_SECONDS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, object] | None = None,
        params: dict[str, str | int] | None = None,
        timeout: float = _QUERY_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        try:
            with httpx.Client(base_url=self._base_url, timeout=timeout) as client:
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
            raise _manager_rejection(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise _manager_unavailable() from exc
        if not isinstance(body, dict):
            raise _manager_unavailable()
        return {str(key): value for key, value in body.items()}


def _manager_rejection(response: httpx.Response) -> HubError:
    """Relay a refusal the manager made about the request, not about the Hub.

    The manager answers 4xx for input it will not act on and names the rule in
    ``detail`` (``SERVICE_IMAGE_MISSING`` and friends). Reporting that as a 502
    "manager unavailable" told the caller the Hub was broken and threw the reason
    away, so the operator could not tell a bad image from a dead process. When the
    body carries no recognisable reason the old 502 is the honest answer.
    """
    reason = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        candidate = payload.get("detail")
        if isinstance(candidate, str) and candidate:
            reason = candidate
    if not reason:
        return HubError(
            code="SERVICE_MANAGER_REJECTED",
            message="服务管理器拒绝请求",
            status_code=502,
        )
    return HubError(
        code=reason,
        message=f"服务管理器拒绝了该请求: {reason}",
        status_code=422,
    )


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
