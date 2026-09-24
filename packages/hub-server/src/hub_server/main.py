"""ASGI application factory for Python Service Hub."""

import contextlib
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
from hub_server.routers.internal_runner import router as internal_runner_router
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


def _sweep_stale_job_temporaries(storage: LocalStorage) -> None:
    """Clean Job staging directories orphaned by a crash between stage and install."""
    from hub_server.services.workspaces import JobWorkspaceService

    try:
        removed = JobWorkspaceService(storage).sweep_stale_temporaries()
    except Exception:
        _LOGGER.exception("Unable to sweep stale Job temporary directories")
        return
    if removed:
        _LOGGER.info("已清理 %d 个过期的 Job 临时工作区目录", removed)


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
            bootstrap_root = Path(settings.storage.root)
            bootstrap_file = bootstrap_root / "bootstrap-admin.json"

            # The credential must land on disk before the account exists. Writing it
            # afterwards used to leave a committed admin whose password nobody held,
            # and the OSError then escaped the lifespan and killed the process.
            try:
                bootstrap_root.mkdir(parents=True, exist_ok=True)
                bootstrap_file.write_text(
                    json.dumps({"username": "admin", "password": password}, ensure_ascii=False),
                    encoding="utf-8",
                )
                bootstrap_file.chmod(0o600)
            except OSError:
                _LOGGER.warning("管理员凭据不可写入, 放弃本次播种: %s", bootstrap_file)
                return

            try:
                session.add(
                    UserRecord(
                        username="admin",
                        password_hash=hash_password(password),
                        role="admin",
                        must_change_password=True,
                    )
                )
                session.commit()
            except Exception:
                # Never leave the credential behind without its account.
                with contextlib.suppress(OSError):
                    bootstrap_file.unlink()
                raise
    finally:
        engine.dispose()

    _LOGGER.info("初始管理员凭据已写入 %s", bootstrap_file)


def create_app(settings: HubSettings | None = None) -> FastAPI:
    """Create a configured Hub ASGI application."""
    if settings is None:
        config_path = os.environ.get("HUB_CONFIG_PATH")
        if not config_path:
            raise RuntimeError(
                "HUB_CONFIG_PATH 环境变量未设置, 无法定位 Hub 配置文件"
            )
        settings = HubSettings.from_yaml(Path(config_path))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _ensure_sqlite_database_directory(settings.database.url)
        command.upgrade(_alembic_config(settings.database.url), "head")
        engine, session_factory = create_engine_and_session_factory(settings.database.url)
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.storage = LocalStorage(settings.storage.root)
        _sweep_stale_job_temporaries(app.state.storage)
        _bootstrap_admin(settings)
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Python Service Hub", version=settings.hub_version, lifespan=lifespan)
    app.state.settings = settings

    # Resolve the enabled plugin set and mount routers in dependency order.
    from hub_server.kernel.protocol import BaseFeaturePlugin
    from hub_server.kernel.registry import resolve_plugins
    from hub_server.plugins.audit_plugin import AuditPlugin
    from hub_server.plugins.auth_plugin import AuthPlugin
    from hub_server.plugins.backup_plugin import BackupPlugin
    from hub_server.plugins.catalog_plugin import CatalogPlugin
    from hub_server.plugins.files_plugin import FilesPlugin
    from hub_server.plugins.jobs_plugin import JobsPlugin
    from hub_server.plugins.metrics_plugin import MetricsPlugin
    from hub_server.plugins.quotas_plugin import QuotasPlugin
    from hub_server.plugins.schedules_plugin import SchedulesPlugin
    from hub_server.plugins.webhooks_plugin import WebhooksPlugin
    from hub_server.routers.audit import router as audit_router
    from hub_server.routers.pipelines import router as pipelines_router
    from hub_server.routers.services import router as services_router

    plugin_catalog: dict[str, BaseFeaturePlugin] = {
        p.name: p
        for p in (
            AuthPlugin(),
            FilesPlugin(),
            JobsPlugin(),
            CatalogPlugin(),
            SchedulesPlugin(),
            WebhooksPlugin(),
            BackupPlugin(),
            MetricsPlugin(),
            QuotasPlugin(),
            AuditPlugin(),
        )
    }
    enabled_plugins = resolve_plugins(plugin_catalog, settings.plugins.enabled)

    for plugin in enabled_plugins:
        for router in plugin.routers:
            app.include_router(router, prefix="/api/v1")

    # Non-plugin routers (internal runner protocol, system health/info).
    app.include_router(internal_runner_router, prefix="/internal/v1")
    app.include_router(system_router, prefix="/api/v1")
    # Services and audit are governance domains not yet wrapped as plugins;
    # mounted directly to avoid breaking existing endpoints.
    app.include_router(services_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(pipelines_router, prefix="/api/v1")

    @app.exception_handler(HubError)
    async def hub_error_handler(_: Request, error: HubError) -> JSONResponse:
        return _error_response(
            error.code, error.message, error.status_code, error.details, error.headers
        )

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


def _error_response(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Serialize one client-safe failure envelope."""
    payload = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status_code, content=payload.model_dump(mode="json"), headers=headers
    )


def _http_error_code_and_message(status_code: int) -> tuple[str, str]:
    """Map framework HTTP failures to stable messages without exposing their details."""
    if status_code == 404:
        return "HTTP_NOT_FOUND", "请求的资源不存在"
    if status_code == 405:
        return "HTTP_METHOD_NOT_ALLOWED", "请求方法不被允许"
    return "HTTP_ERROR", "请求失败"


app = create_app()
