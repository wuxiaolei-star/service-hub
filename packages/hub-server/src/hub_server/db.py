"""SQLAlchemy setup for Hub metadata."""

from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base shared by Hub database models."""


def create_engine_and_session_factory(url: str) -> tuple[Engine, sessionmaker[Session]]:
    """Create a SQLite engine and sessions hardened for multi-process use.

    Several Hub processes (hub-api, cleaner, scheduler, both runners) write to
    the same database file. WAL lets readers proceed during writes, and a
    30-second busy timeout makes writers queue instead of failing with
    "database is locked"; the connect timeout covers the driver-level lock
    wait for the first statement of a fresh connection.
    """
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    return engine, sessionmaker(bind=engine)
