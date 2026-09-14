"""ASGI application factory for Python Service Hub."""

import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url
from starlette.exceptions import HTTPException as StarletteHTTPException

from alembic import command
from hub_server.db import create_engine_and_session_factory
from hub_server.errors import HubError
from hub_server.routers.audit import router as audit_router
from hub_server.routers.auth import router as auth_router
from hub_server.routers.files import router as files_router
from hub_server.routers.internal_runner import router as internal_runner_router
from hub_server.routers.jobs import router as jobs_router
from hub_server.routers.logs_stream import router as logs_stream_router
from hub_server.routers.pipelines import router as pipelines_router
from hub_server.routers.plugins import router as plugins_router
from hub_server.routers.registry import router as registry_router
from hub_server.routers.schedules import router as schedules_router
from hub_server.routers.services import router as services_router
from hub_server.routers.system import router as system_router
from hub_server.routers.users import key_router as api_keys_router
from hub_server.routers.users import router as users_router
from hub_server.routers.webhooks import router as webhooks_router
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
        _bootstrap_admin(settings)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Python Service Hub", version=settings.hub_version, lifespan=lifespan)
    app.state.settings = settings
    app.include_router(system_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(api_keys_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(schedules_router, prefix="/api/v1")
    app.include_router(services_router, prefix="/api/v1")
    app.include_router(webhooks_router, prefix="/api/v1")
    app.include_router(pipelines_router, prefix="/api/v1")
    app.include_router(registry_router, prefix="/api/v1")
    app.include_router(logs_stream_router, prefix="/api/v1")
    app.include_router(files_router, prefix="/api/v1")
    app.include_router(plugins_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(internal_runner_router, prefix="/internal/v1")

    @app.exception_handler(HubError)
    async def hub_error_handler(_: Request, error: HubError) -> JSONResponse:
        return _error_response(error.code, error.message, error.status_code, error.details)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _: Request, __: RequestValidationError
    ) -> JSONResponse:
        return _error_response("REQUEST_VALIDATION_ERROR", "请求参数无效", 422)

    async def http_error_handler(_: Request, error: Exception) -> JSONResponse:
        if not isinstance(error, StarletteHTTPException):
            return _error_response("UNEXPECTED_ERROR", "服务器内部错误", 500)
        code, message = _http_error_code_and_message(error.status_code)
        return _error_response(code, message, error.status_code)

    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
        _LOGGER.exception("Unhandled Hub API error", exc_info=error)
        return _error_response("UNEXPECTED_ERROR", "服务器内部错误", 500)

    return app


def _bootstrap_admin(settings: HubSettings) -> None:
    """Seed the first admin account into a protected file on first run."""
    if settings.auth.mode != "required":
        return
    from hub_server.db import create_engine_and_session_factory
    from hub_server.models import UserRecord
    from hub_server.services.auth import hash_password

    engine, session_factory = create_engine_and_session_factory(settings.database.url)
    try:
        with session_factory() as session:
            if session.query(UserRecord).count() > 0:
                return
            password = secrets.token_urlsafe(18)
            session.add(
                UserRecord(
                    username="admin",
                    password_hash=hash_password(password),
                    role="admin",
                    must_change_password=True,
                )
            )
            session.commit()
    finally:
        engine.dispose()

    bootstrap_root = Path(settings.storage.root)
    bootstrap_root.mkdir(parents=True, exist_ok=True)
    bootstrap_file = bootstrap_root / "bootstrap-admin.json"
    bootstrap_file.write_text(
        json.dumps({"username": "admin", "password": password}, ensure_ascii=False),
        encoding="utf-8",
    )
    bootstrap_file.chmod(0o600)
    _LOGGER.info("初始管理员凭据已写入 %s", bootstrap_file)


def _error_response(
    code: str, message: str, status_code: int, details: dict[str, object] | None = None
) -> JSONResponse:
    """Serialize one client-safe failure envelope."""
    payload = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _http_error_code_and_message(status_code: int) -> tuple[str, str]:
    """Map framework HTTP failures to stable messages without exposing their details."""
    if status_code == 404:
        return "HTTP_NOT_FOUND", "请求的资源不存在"
    if status_code == 405:
        return "HTTP_METHOD_NOT_ALLOWED", "请求方法不被允许"
    return "HTTP_ERROR", "请求失败"


app = create_app()
