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


@app.get("/ping")
def ping(_: Annotated[None, Depends(_require_runner_token)]) -> dict[str, str]:
    return {"status": "ok"}


@app.post("/deploy")
def deploy(
    spec: DeployRequest, _: Annotated[None, Depends(_require_runner_token)]
) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.post("/{name}/stop")
def stop(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.post("/{name}/start")
def start(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.post("/{name}/restart")
def restart(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.delete("/{name}")
def remove(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.get("/{name}/status")
def status(name: str, _: Annotated[None, Depends(_require_runner_token)]) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


@app.get("/{name}/logs")
def logs(
    name: str,
    _: Annotated[None, Depends(_require_runner_token)],
    tail: int = 200,
) -> dict[str, object]:
    raise HTTPException(status_code=501, detail="not implemented")


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
