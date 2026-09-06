"""ASGI application factory for Python Service Hub."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url

from alembic import command
from hub_server.db import create_engine_and_session_factory
from hub_server.errors import HubError
from hub_server.routers.files import router as files_router
from hub_server.routers.system import router as system_router
from hub_server.schemas import ErrorBody, ErrorResponse
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

_LOGGER = logging.getLogger(__name__)


def _alembic_config(database_url: str) -> Config:
    """Build an Alembic configuration scoped to this application instance."""
    project_root = Path(__file__).resolve().parents[4]
    config = Config(str(project_root / "alembic.ini"))
    config.attributes["database_url"] = database_url
    return config


def _ensure_sqlite_database_directory(database_url: str) -> None:
    """Create the parent directory needed by a configured file-backed SQLite database."""
    url = make_url(database_url)
    database = url.database
    if url.drivername.startswith("sqlite") and database is not None and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def create_app(settings: HubSettings | None = None) -> FastAPI:
    """Create a configured Hub ASGI application."""
    if settings is None:
        settings = HubSettings.from_yaml(Path(os.environ["HUB_CONFIG_PATH"]))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _ensure_sqlite_database_directory(settings.database.url)
        command.upgrade(_alembic_config(settings.database.url), "head")
        engine, session_factory = create_engine_and_session_factory(settings.database.url)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.storage = LocalStorage(settings.storage.root)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Python Service Hub", version=settings.hub_version, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(files_router, prefix="/api/v1")

    @app.exception_handler(HubError)
    async def hub_error_handler(_: Request, error: HubError) -> JSONResponse:
        return _error_response(error.code, error.message, error.status_code, error.details)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
        _LOGGER.exception("Unhandled Hub API error", exc_info=error)
        return _error_response("UNEXPECTED_ERROR", "服务器内部错误", 500)

    return app


def _error_response(
    code: str, message: str, status_code: int, details: dict[str, object] | None = None
) -> JSONResponse:
    """Serialize one client-safe failure envelope."""
    payload = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


app = create_app()
