"""Feature plugin protocol for the Hub kernel.

A FeaturePlugin is a *kernel-level* functional domain (auth, files, jobs...),
not a business plugin: business plugins (Compute Plugins) are end-user
algorithm packages managed by ``models.Plugin``/``routers.plugins``.
The two concepts never mix.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """One emitted domain event, fanned out to in-process subscribers."""

    event_type: str  # e.g. "hub.job.completed"
    payload: dict[str, Any]
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_process: str = "hub-api"


EventHandler = Callable[[EventEnvelope], None]


@dataclass(frozen=True, slots=True)
class EventBinding:
    """Subscribe one handler to one event type.

    ``deliver_via_database=True`` makes the event survive the emitting process:
    it is persisted into ``hub_events`` and redelivered by the scheduler's
    ``drain_events`` task, matching at-least-once polling semantics.
    """

    event_type: str
    handler: EventHandler
    deliver_via_database: bool = False


@dataclass(frozen=True, slots=True)
class SchedulerTaskSpec:
    """One periodic sweep; identical contract to scheduler_service.register_task."""

    name: str
    run: Callable[[Any], int]  # receives a Session, returns items processed
    processes: tuple[str, ...] = ("scheduler",)


@dataclass(frozen=True, slots=True)
class RouterMount:
    """One APIRouter plus the prefix it is mounted under."""

    router: Any  # fastapi.APIRouter
    prefix: str = "/api/v1"


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Everything the kernel hands a plugin; identical objects on every process."""

    settings: Any  # HubSettings
    session_factory: Any  # sessionmaker[Session]
    storage: Any  # LocalStorage
    event_bus: Any  # EventBus
    process: str  # "hub-api" | "scheduler" | "cleaner" | ...
    plugin_config: dict[str, Any]  # settings.plugins.options[name]


class BaseFeaturePlugin:
    """Recommended base class: empty declarative defaults, no-op hooks.

    Using a base class (instead of bare Protocol) lets the kernel enumerate
    attributes without getattr dances, while plugins stay free to override.
    """

    name: str = ""
    depends_on: tuple[str, ...] = ()
    soft_depends_on: tuple[str, ...] = ()
    models: tuple[type, ...] = ()
    default_enabled: bool = True
    routers: tuple[Any, ...] = ()
    scheduler_tasks: tuple[SchedulerTaskSpec, ...] = ()
    event_handlers: tuple[EventBinding, ...] = ()
    settings_model: type[BaseModel] | None = None

    def on_load(self, ctx: PluginContext) -> None:
        """Bind runtime objects; must not touch the database schema."""

    def bootstrap(self, ctx: PluginContext) -> None:
        """Seed data once (e.g. admin account); runs after routers are mounted."""
