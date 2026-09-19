"""Audit feature plugin: immutable operation trail and its retention sweep."""

from __future__ import annotations

from sqlalchemy.orm import Session

from hub_server.kernel.protocol import (
    BaseFeaturePlugin,
    PluginContext,
    SchedulerTaskSpec,
)
from hub_server.models import AuditLogRecord
from hub_server.routers.audit import router as audit_router
from hub_server.services.retention import sweep_old_audit_logs

_loaded_context: PluginContext | None = None


def _run_delete_old_audit_logs(session: Session) -> int:
    """Adapt ``sweep_old_audit_logs`` to the kernel sweep contract.

    The kernel hands a scheduler task a Session only; the ``RetentionSettings``
    section comes from the PluginContext that :meth:`AuditPlugin.on_load` bound
    in the process running the sweep. The deploy scheduler still registers its
    own ``delete_old_audit_logs`` closure until the kernel runner takes over
    task registration, so raising here keeps an unloaded plugin from silently
    double-sweeping audit rows.
    """
    if _loaded_context is None:
        raise RuntimeError(
            "delete_old_audit_logs ran before AuditPlugin.on_load bound its context"
        )
    return sweep_old_audit_logs(session, _loaded_context.settings.retention)


delete_old_audit_logs_task = SchedulerTaskSpec(
    name="delete_old_audit_logs",
    run=_run_delete_old_audit_logs,
    processes=("scheduler",),
)


class AuditPlugin(BaseFeaturePlugin):
    """Wrap the audit trail domain as one swappable FeaturePlugin.

    ``AuditLogRecord`` stands alone (no FKs into other domains) and is written
    by every other write path through ``services.audit.record``, so no
    ``depends_on`` is declared: a disabled audit domain must never break the
    domains that feed it.
    """

    name = "audit"
    models = (AuditLogRecord,)
    routers = (audit_router,)
    scheduler_tasks = (delete_old_audit_logs_task,)

    def on_load(self, ctx: PluginContext) -> None:
        global _loaded_context
        _loaded_context = ctx
