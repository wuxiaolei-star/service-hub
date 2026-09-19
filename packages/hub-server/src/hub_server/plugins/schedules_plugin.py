"""Schedules feature plugin: periodic Job creation from Schedule rules."""

from __future__ import annotations

from sqlalchemy.orm import Session

from hub_server.kernel.protocol import (
    BaseFeaturePlugin,
    PluginContext,
    SchedulerTaskSpec,
)
from hub_server.models import Schedule
from hub_server.routers.schedules import router as schedules_router
from hub_server.services.schedules import trigger_due

_loaded_context: PluginContext | None = None


def _run_trigger_schedules(session: Session) -> int:
    """Adapt ``trigger_due`` to the kernel sweep contract (session -> count).

    The kernel hands a scheduler task a Session only; storage and settings come
    from the PluginContext that :meth:`SchedulesPlugin.on_load` bound in the
    process running the sweep. The deploy scheduler still registers its own
    ``trigger_schedules`` closure until the kernel runner takes over task
    registration, so raising here keeps an unloaded plugin from silently
    double-triggering schedules.
    """
    if _loaded_context is None:
        raise RuntimeError("trigger_schedules ran before SchedulesPlugin.on_load bound its context")
    return trigger_due(session, _loaded_context.storage, _loaded_context.settings)


trigger_schedules_task = SchedulerTaskSpec(
    name="trigger_schedules",
    run=_run_trigger_schedules,
    processes=("scheduler",),
)


class SchedulesPlugin(BaseFeaturePlugin):
    """Wrap the schedules domain as one swappable FeaturePlugin.

    ``soft_depends_on = ("jobs",)`` encodes the runtime coupling: a schedule
    trigger creates Jobs through ``JobService``, so a disabled jobs domain
    degrades every sweep into denied audits rather than breaking startup.
    """

    name = "schedules"
    depends_on = ()
    soft_depends_on = ("jobs",)
    models = (Schedule,)
    default_enabled = True
    routers = (schedules_router,)
    scheduler_tasks = (trigger_schedules_task,)

    def on_load(self, ctx: PluginContext) -> None:
        global _loaded_context
        _loaded_context = ctx
