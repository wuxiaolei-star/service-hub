"""Internal service manager exposing Docker operations to the Hub API.

V3.0: this is the only process besides docker-runner that touches the Docker
Socket. It listens on 127.0.0.1:8001 inside the container, authenticates with
the shared runner token, and executes the desired state that hub-api forwards.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

_LOGGER = logging.getLogger("hub.service-manager")

_PORT = int(os.environ.get("HUB_SERVICE_MANAGER_PORT", "8001"))


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
    authorization = request.headers.get("authorization")
    expected = os.environ.get("HUB_RUNNER_TOKEN", "")
    if not expected or authorization != f"Bearer {expected}":
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


def _to_docker_ports(ports: list[dict[str, int]]) -> dict[str, int]:
    """Convert API port mappings to docker-py's ``{"127.0.0.1:HOST": CONTAINER}`` format."""
    return {f"127.0.0.1:{mapping['host']}": mapping["container"] for mapping in ports}


def _to_docker_volumes(mounts: list[dict[str, object]]) -> dict[str, dict[str, str]]:
    """Convert API mounts to docker-py's ``{SOURCE: {"bind": TARGET, "mode": MODE}}`` format."""
    volumes: dict[str, dict[str, str]] = {}
    for mount in mounts:
        mode = "ro" if mount.get("read_only") else "rw"
        volumes[str(mount["source"])] = {"bind": str(mount["target"]), "mode": mode}
    return volumes


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
