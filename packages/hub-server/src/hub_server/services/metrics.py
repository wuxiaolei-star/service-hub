"""Prometheus text exposition built from live database counts."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from hub_server.models import (
    ApiKeyRecord,
    AuditLogRecord,
    FileRecord,
    Job,
    PluginBuild,
    SessionRecord,
    UserRecord,
)
from hub_server.settings import HubSettings

ACTIVE_JOB_STATUSES = ("PENDING", "PREPARING", "RUNNING", "CANCEL_REQUESTED")

_PROCESS_START = time.time()


def _gauge(name: str, help_text: str, value: int | float) -> list[str]:
    return [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {value}"]


def render_metrics(session: Session, settings: HubSettings) -> str:
    """Render the Prometheus exposition for one scrape."""
    lines: list[str] = []

    job_status_counts = (
        session.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
    )
    lines.append("# HELP hub_jobs_total Jobs grouped by status.")
    lines.append("# TYPE hub_jobs_total gauge")
    if job_status_counts:
        for status_value, count in job_status_counts:
            lines.append(f'hub_jobs_total{{status="{status_value}"}} {count}')
    else:
        lines.append('hub_jobs_total{status="none"} 0')
    active = (
        session.query(func.count(Job.id))
        .filter(Job.status.in_(ACTIVE_JOB_STATUSES))
        .scalar()
        or 0
    )
    lines.extend(_gauge("hub_jobs_active", "Non-terminal jobs.", active))

    files_bytes = session.query(func.coalesce(func.sum(FileRecord.size_bytes), 0)).scalar()
    files_total = session.query(func.count(FileRecord.id)).scalar()
    lines.extend(_gauge("hub_files_total", "Stored files.", files_total or 0))
    lines.extend(
        _gauge("hub_files_bytes_total", "Stored file bytes.", files_bytes or 0)
    )

    build_status_counts = (
        session.query(PluginBuild.status, func.count(PluginBuild.id))
        .group_by(PluginBuild.status)
        .all()
    )
    lines.append("# HELP hub_plugin_builds_total Builds grouped by status.")
    lines.append("# TYPE hub_plugin_builds_total gauge")
    if build_status_counts:
        for status_value, count in build_status_counts:
            lines.append(f'hub_plugin_builds_total{{status="{status_value}"}} {count}')
    else:
        lines.append('hub_plugin_builds_total{status="none"} 0')

    users_total = session.query(func.count(UserRecord.id)).scalar()
    lines.extend(_gauge("hub_users_total", "Registered users.", users_total or 0))

    now = datetime.now(UTC)
    sessions_active = (
        session.query(func.count(SessionRecord.id))
        .filter(
            SessionRecord.revoked_at.is_(None),
            SessionRecord.expires_at > now.replace(tzinfo=None),
        )
        .scalar()
    )
    lines.extend(_gauge("hub_sessions_active", "Unexpired sessions.", sessions_active or 0))

    quota_limit = settings.quotas.max_total_bytes if settings.quotas.enabled else 0
    lines.extend(
        _gauge(
            "hub_quota_global_bytes_total",
            "Configured global byte quota (0 when disabled).",
            quota_limit,
        )
    )

    lines.extend(
        _gauge(
            "hub_uptime_seconds",
            "Seconds since the API process started.",
            round(time.time() - _PROCESS_START, 1),
        )
    )

    audit_total = session.query(func.count(AuditLogRecord.id)).scalar()
    lines.extend(_gauge("hub_audit_entries_total", "Audit rows.", audit_total or 0))

    api_keys_active = (
        session.query(func.count(ApiKeyRecord.id))
        .filter(ApiKeyRecord.revoked_at.is_(None))
        .scalar()
    )
    lines.extend(_gauge("hub_api_keys_active", "Unrevoked API keys.", api_keys_active or 0))

    return "\n".join(lines) + "\n"
