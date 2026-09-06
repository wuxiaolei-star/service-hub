"""ASGI application factory for Python Service Hub."""

from fastapi import FastAPI

from hub_server.routers.system import router as system_router
from hub_server.settings import HubSettings, default_settings


def create_app(settings: HubSettings | None = None) -> FastAPI:
    """Create a configured Hub ASGI application."""
    configured_settings = settings or default_settings()
    app = FastAPI(title="Python Service Hub", version=configured_settings.hub_version)
    app.state.settings = configured_settings
    app.include_router(system_router, prefix="/api/v1")
    return app


app = create_app()
