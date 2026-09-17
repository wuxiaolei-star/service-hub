"""Internal service manager exposing Docker operations to the Hub API.

V3.0: this is the only process besides docker-runner that touches the Docker
Socket. It listens on 127.0.0.1:8001 inside the container, authenticates with
the shared runner token, and executes the desired state that hub-api forwards.

Every deploy request is re-validated here (non-root user, unreserved host
ports, host-data-root mount allowlist, image present locally) so the container
boundary holds even if a future caller bypasses the public API validation.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import sys
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

_LOGGER = logging.getLogger("hub.service-manager")

_PORT = int(os.environ.get("HUB_SERVICE_MANAGER_PORT", "8001"))

# Mirrors hub_server.schemas: unprivileged host ports that never collide with
# the Hub's own loopback listeners (hub-api 8000, this manager 8001, web 8080).
_HOST_PORT_MIN = 1024
_HOST_PORT_MAX = 65535
_RESERVED_HOST_PORTS = frozenset({8000, 8001, 8080})
_NON_ROOT_USER_PATTERN = re.compile(r"^[1-9]\d{0,9}:[1-9]\d{0,9}$")


class DeployRequest(BaseModel):
    name: str = Field(min_length=1, max_length=63)
    image: str = Field(min_length=1)
    ports: list[dict[str, int]] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    mounts: list[dict[str, object]] = Field(default_factory=list)
    command: list[str] | None = None
    user_label: str = "65532:65532"


app = FastAPI(title="Hub Service Manager", docs_url=None, redoc_url=None)


def _require_runner_token(request: Request) -> None:
    authorization = request.headers.get("authorization") or ""
    expected = os.environ.get("HUB_RUNNER_TOKEN", "")
    # Constant-time comparison, matching hub_server's internal_runner policy:
    # this token authorizes Docker operations, so never leak match progress.
    if not expected or not hmac.compare_digest(
        authorization.encode(), f"Bearer {expected}".encode()
    ):
        raise HTTPException(status_code=401, detail="invalid runner token")


def _docker_client() -> Any:
    """Docker client factory; overridable for tests."""
    import docker

    return docker.from_env()


def _not_found_error() -> type[Exception]:
    """docker.errors.NotFound, imported lazily like the client factory."""
    from docker.errors import NotFound

    return NotFound


def _container_name(name: str) -> str:
    return f"hub-svc-{name}"


def _to_docker_ports(ports: list[dict[str, int]]) -> dict[str, tuple[str, int]]:
    """Convert API port mappings to docker-py's ``{CONTAINER/tcp: (HOST_IP, PORT)}`` form.

    docker-py keys the mapping by the *container* port and takes the host side as
    the value. A ``"127.0.0.1:HOST"`` key instead travels to the daemon as a bare
    port string, which it rejects with ``400 ... invalid port``, so no managed
    service could ever be created. The tuple value is also what pins the published
    port to loopback, which is what the deployment model requires.
    """
    return {f"{mapping['container']}/tcp": ("127.0.0.1", mapping["host"]) for mapping in ports}


def _to_docker_volumes(mounts: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    """Convert API mounts to docker-py's ``{SOURCE: {"bind": TARGET, "mode": MODE}}`` format."""
    volumes: dict[str, dict[str, str]] = {}
    for mount in mounts:
        mode = "ro" if mount.get("read_only") else "rw"
        volumes[str(mount["source"])] = {"bind": str(mount["target"]), "mode": mode}
    return volumes


def _validate_deploy_spec(spec: DeployRequest) -> None:
    """Re-enforce the public API contract before any Docker call is made.

    These checks mirror hub_server.schemas and routers.services so the Hub's
    boundary rules hold even for callers that speak to the manager directly.
    """
    if _NON_ROOT_USER_PATTERN.fullmatch(spec.user_label) is None:
        raise HTTPException(status_code=422, detail="SERVICE_USER_FORBIDDEN")
    for mapping in spec.ports:
        host = mapping.get("host")
        if (
            not isinstance(host, int)
            or not _HOST_PORT_MIN <= host <= _HOST_PORT_MAX
            or host in _RESERVED_HOST_PORTS
        ):
            raise HTTPException(status_code=422, detail="SERVICE_PORT_FORBIDDEN")
    _validate_mounts(spec.mounts)


def _validate_mounts(mounts: list[dict[str, object]]) -> None:
    """Bind mounts may only target directories below the Hub host data root."""
    root = os.environ.get("HUB_DOCKER_HOST_DATA_ROOT", "").rstrip("/")
    for mount in mounts:
        source = str(mount["source"])
        if (
            not root
            or not source.startswith(root + "/")
            or source == root
            or ".." in source.split("/")
        ):
            raise HTTPException(status_code=422, detail="SERVICE_MOUNT_FORBIDDEN")


def _require_local_image(client: Any, image: str) -> None:
    """Deploy only images already present on the host; never implicitly pull.

    The offline deployment model requires operators to import images before
    use, so an unknown image is a configuration error rather than a trigger
    for a network fetch.
    """
    from docker.errors import ImageNotFound

    try:
        client.images.get(image)
    except ImageNotFound as exc:
        raise HTTPException(status_code=422, detail="SERVICE_IMAGE_MISSING") from exc


def _get_container(client: Any, container_name: str) -> Any:
    try:
        return client.containers.get(container_name)
    except _not_found_error() as exc:
        raise HTTPException(status_code=404, detail="SERVICE_NOT_FOUND") from exc


@app.get("/ping")
def ping(_: Annotated[None, Depends(_require_runner_token)]) -> dict[str, str]:
    return {"status": "ok"}


@app.post("/deploy")
def deploy(
    spec: DeployRequest, _: Annotated[None, Depends(_require_runner_token)]
) -> dict[str, object]:
    client = _docker_client()
    _validate_deploy_spec(spec)
    _require_local_image(client, spec.image)
    container_name = _container_name(spec.name)
    try:
        existing = client.containers.get(container_name)
    except _not_found_error():
        _LOGGER.info("no existing container %s, deploying fresh", container_name)
    else:
        _LOGGER.info("replacing existing container %s", container_name)
        existing.remove(force=True)
    container = client.containers.run(
        spec.image,
        detach=True,
        name=container_name,
        ports=_to_docker_ports(spec.ports),
        environment=dict(spec.env),
        volumes=_to_docker_volumes(spec.mounts),
        command=spec.command,
        user=spec.user_label,
        restart_policy={"Name": "unless-stopped"},
    )
    _LOGGER.info("deployed container %s from image %s", container_name, spec.image)
    return {"name": container_name, "state": container.status}


@app.post("/{name}/stop")
def stop(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    container.stop()
    return {"name": _container_name(name), "state": container.status}


@app.post("/{name}/start")
def start(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    container.start()
    return {"name": _container_name(name), "state": container.status}


@app.post("/{name}/restart")
def restart(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    container.restart()
    return {"name": _container_name(name), "state": container.status}


@app.delete("/{name}")
def remove(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    container.remove(force=True)
    return {"name": _container_name(name), "state": "REMOVED"}


@app.get("/{name}/status")
def status(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    attrs: dict[str, Any] = container.attrs
    state = attrs.get("State", {})
    return {
        "name": _container_name(name),
        "state": state.get("Status"),
        "health": state.get("Health", {}).get("Status"),
        "ports": attrs.get("NetworkSettings", {}).get("Ports"),
        "image": attrs.get("Config", {}).get("Image"),
        "created_at": attrs.get("Created"),
    }


@app.get("/{name}/logs")
def logs(
    name: str,
    _: Annotated[None, Depends(_require_runner_token)],
    tail: int = 200,
) -> dict[str, object]:
    client = _docker_client()
    container = _get_container(client, _container_name(name))
    raw: bytes = container.logs(tail=tail, timestamps=False)
    return {"name": _container_name(name), "logs": raw.decode("utf-8", errors="replace")}


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
