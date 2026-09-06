"""SQLAlchemy setup for Hub metadata."""

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base shared by Hub database models."""


def create_engine_and_session_factory(url: str) -> tuple[Engine, sessionmaker[Session]]:
    """Create a SQLite engine and sessions with referential integrity enabled."""
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine, sessionmaker(bind=engine)
