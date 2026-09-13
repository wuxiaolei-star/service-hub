"""Runtime health checks for the single-container Service Hub."""

from __future__ import annotations

import http.client
import socket
import sys
import xmlrpc.client
from collections.abc import Mapping
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

_API_HEALTH_URL = "http://127.0.0.1:8000/api/v1/system/health"
_DATA_ROOT = Path("/data")
_SUPERVISOR_SOCKET = "/run/service-hub/supervisor.sock"
_REQUIRED_PROCESSES = ("hub-api", "conda-runner", "docker-runner", "cleaner")


class HealthcheckError(RuntimeError):
    """Raised when a required Service Hub health dependency is unavailable."""


class _UnixSocketHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection that routes XML-RPC over a local Unix socket."""

    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost")
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
        self.sock.connect(self._socket_path)


class _UnixSocketTransport(xmlrpc.client.Transport):
    """Provide the XML-RPC transport served by supervisord's Unix socket."""

    def __init__(self, socket_path: str) -> None:
        super().__init__()
        self._socket_path = socket_path

    def make_connection(
        self, host: str | tuple[str, dict[str, str]]
    ) -> http.client.HTTPConnection:
        del host
        return _UnixSocketHTTPConnection(self._socket_path)


def check_health(
    process_states: Mapping[str, str], api_up: bool, data_writable: bool, docker_up: bool
) -> None:
    """Raise a safe error unless every required runtime dependency is healthy."""
    for process_name in _REQUIRED_PROCESSES:
        if process_states.get(process_name) != "RUNNING":
            raise HealthcheckError(f"supervised process {process_name} is not RUNNING")
    if not api_up:
        raise HealthcheckError("Hub API is unavailable")
    if not data_writable:
        raise HealthcheckError("/data is not writable")
    if not docker_up:
        raise HealthcheckError("Docker Engine is unavailable")


def _supervisor_process_states() -> dict[str, str]:
    """Return state names for all supervisord-managed processes."""
    try:
        proxy = xmlrpc.client.ServerProxy(
            "http://localhost/RPC2",
            transport=_UnixSocketTransport(_SUPERVISOR_SOCKET),
            allow_none=True,
        )
        result: object = proxy.supervisor.getAllProcessInfo()
    except (OSError, xmlrpc.client.Error) as error:
        raise HealthcheckError("supervisor is unavailable") from error

    if not isinstance(result, list):
        raise HealthcheckError("supervisor returned an invalid process state")

    process_states: dict[str, str] = {}
    for process in result:
        if not isinstance(process, dict):
            raise HealthcheckError("supervisor returned an invalid process state")
        name = process.get("name")
        state = process.get("statename")
        if not isinstance(name, str) or not isinstance(state, str):
            raise HealthcheckError("supervisor returned an invalid process state")
        process_states[name] = state
    return process_states


def _api_is_up() -> bool:
    """Return whether the public health endpoint responded successfully."""
    try:
        with urlopen(_API_HEALTH_URL, timeout=5) as response:
            return bool(response.getcode() == 200)
    except (OSError, URLError):
        return False


def _data_is_writable() -> bool:
    """Exercise the mounted data volume without leaving a healthcheck artifact behind."""
    probe = _DATA_ROOT / ".healthcheck"
    try:
        with probe.open("w", encoding="utf-8") as handle:
            handle.write("ok\n")
        probe.unlink()
    except OSError:
        with suppress(OSError):
            probe.unlink()
        return False
    return True


def _docker_is_up() -> bool:
    """Use the Docker SDK to verify that the mounted Engine socket responds."""
    try:
        docker = import_module("docker")
        client = docker.from_env()
        try:
            client.ping()
        finally:
            client.close()
    except Exception:
        return False
    return True


def main() -> int:
    """Run health probes and emit one secret-free failure line when one is unavailable."""
    try:
        check_health(
            process_states=_supervisor_process_states(),
            api_up=_api_is_up(),
            data_writable=_data_is_writable(),
            docker_up=_docker_is_up(),
        )
    except HealthcheckError as error:
        print(f"service-hub healthcheck failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# V2.1: cleaner 加入必需进程列表
