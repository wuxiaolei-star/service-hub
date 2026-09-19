"""Jobs feature plugin: Job lifecycle, runner claiming, and log streaming."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from hub_server.kernel.protocol import (
    BaseFeaturePlugin,
    PluginContext,
    SchedulerTaskSpec,
)
from hub_server.models import Job, JobFile
from hub_server.routers.internal_runner import router as internal_runner_router
from hub_server.routers.jobs import router as jobs_router
from hub_server.routers.logs_stream import router as logs_stream_router
from hub_server.services.reaper import reap_stale_jobs
from hub_server.services.retention import sweep_terminal_jobs

_loaded_context: PluginContext | None = None


def _run_delete_retired_jobs(session: Session) -> int:
    """Adapt ``sweep_terminal_jobs`` to the kernel sweep contract (session -> count).

    The kernel hands a scheduler task a Session only; the storage root and the
    retention settings come from the PluginContext that :meth:`JobsPlugin.on_load`
    bound in the process running the sweep. The deploy scheduler still registers
    its own ``delete_retired_jobs`` closure until the kernel runner takes over
    task registration, so raising here keeps an unloaded plugin from silently
    deleting Jobs that should be retained.
    """
    if _loaded_context is None:
        raise RuntimeError("delete_retired_jobs ran before JobsPlugin.on_load bound its context")
    settings = _loaded_context.settings
    return sweep_terminal_jobs(session, Path(settings.storage.root), settings.retention)


reap_lost_jobs_task = SchedulerTaskSpec(
    name="reap_lost_jobs",
    run=reap_stale_jobs,
    processes=("scheduler",),
)

delete_retired_jobs_task = SchedulerTaskSpec(
    name="delete_retired_jobs",
    run=_run_delete_retired_jobs,
    processes=("scheduler",),
)


class JobsPlugin(BaseFeaturePlugin):
    """Wrap the jobs domain as one swappable FeaturePlugin.

    ``depends_on = ("auth", "files")`` encodes the row graph: every Job points
    at its owning User and every JobFile row points at a FileRecord snapshot
    (``file_record_id -> file_records.id``, RESTRICT), so auth and files must
    come up first. ``routers`` also carries the internal runner claim/heartbeat
    API and the SSE log stream, which exist only to serve Job execution.
    """

    name = "jobs"
    depends_on = ("auth", "files")
    models = (Job, JobFile)
    default_enabled = True
    routers = (jobs_router, internal_runner_router, logs_stream_router)
    scheduler_tasks = (reap_lost_jobs_task, delete_retired_jobs_task)

    def on_load(self, ctx: PluginContext) -> None:
        global _loaded_context
        _loaded_context = ctx
