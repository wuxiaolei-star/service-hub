"""FastAPI dependencies backed by the configured Hub application state."""

from collections.abc import Generator
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage


def get_settings(request: Request) -> HubSettings:
    """Return the immutable settings selected when the application was created."""
    return cast(HubSettings, request.app.state.settings)


def get_storage(request: Request) -> LocalStorage:
    """Return the Hub-controlled local storage initialized during application startup."""
    return cast(LocalStorage, request.app.state.storage)


def get_session(request: Request) -> Generator[Session, None, None]:
    """Provide one database session per request and reliably close it afterwards."""
    session_factory = cast(sessionmaker[Session], request.app.state.session_factory)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
