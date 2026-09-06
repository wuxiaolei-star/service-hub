"""ASGI application factory for Python Service Hub."""

import os
from pathlib import Path

from fastapi import FastAPI

from hub_server.routers.system import router as system_router
from hub_server.settings import HubSettings


def create_app(settings: HubSettings | None = None) -> FastAPI:
    """Create a configured Hub ASGI application."""
    if settings is None:
        settings = HubSettings.from_yaml(Path(os.environ["HUB_CONFIG_PATH"]))
    app = FastAPI(title="Python Service Hub", version=settings.hub_version)
    app.state.settings = settings
    app.include_router(system_router, prefix="/api/v1")
    return app


app = create_app()
