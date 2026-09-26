"""Prometheus text exposition built from live database counts."""

from __future__ import annotations

import time
from bisect import bisect_right
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from hub_server.models import (
    ApiKeyRecord,
    AuditLogRecord,
    FileRecord,
    Job,
    Plugin,
    PluginBuild,
    PluginVersion,
    SessionRecord,
    UserRecord,
)
from hub_server.settings import HubSettings

ACTIVE_JOB_STATUSES = ("PENDING", "PREPARING", "RUNNING", "CANCEL_REQUESTED")

# G8 job-duration histogram buckets in seconds, upper bounds inclusive, plus
# the implicit +Inf bucket rendered explicitly.
_JOB_DURATION_BUCKETS_SECONDS: tuple[float, ...] = (0.1, 0.5, 1, 5, 15, 60, 300, 1800)

_PROCESS_START = time.time()
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[float, str]] = {}


def _clear_metrics_cache() -> None:
    """Test hook: drop the process-local metrics cache."""
    _cache.pop("render", None)


def _gauge(name: str, help_text: str, value: int | float) -> list[str]:
    return [f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {value}"]


def render_metrics(session: Session, settings: HubSettings) -> str:
    """Render the Prometheus exposition for one scrape (30s process-local cache)."""
    now = time.monotonic()
    cached = _cache.get("render")
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    body = _render_metrics(session, settings)
    _cache["render"] = (now, body)
    return body


def _render_metrics(session: Session, settings: HubSettings) -> str:
    lines: list[str] = []

    # G8: per-plugin/runtime/status job counter. The values are counts of
    # current Job rows, so they grow as jobs reach a status and only drop when
    # the 30-day retention deletes aged rows; scrape-side rate()/increase()
    # windows far shorter than the retention horizon see an effectively
    # monotonic series.
    job_totals = (
        session.query(Plugin.plugin_key, Job.runtime_type, Job.status, func.count(Job.id))
        .join(PluginBuild, PluginBuild.id == Job.plugin_build_id)
        .join(PluginVersion, PluginVersion.id == PluginBuild.plugin_version_id)
        .join(Plugin, Plugin.id == PluginVersion.plugin_id)
        .group_by(Plugin.plugin_key, Job.runtime_type, Job.status)
        .order_by(Plugin.plugin_key, Job.runtime_type, Job.status)
        .all()
    )
    lines.append("# HELP hub_jobs_total Jobs grouped by plugin, runtime, and status.")
    lines.append("# TYPE hub_jobs_total counter")
    if job_totals:
        for plugin_key, runtime_type, status_value, count in job_totals:
            lines.append(
                f'hub_jobs_total{{plugin="{plugin_key}",runtime="{runtime_type}",'
                f'status="{status_value}"}} {count}'
            )
    else:
        lines.append('hub_jobs_total{plugin="none",runtime="none",status="none"} 0')

    lines.extend(_job_duration_histogram_lines(session))
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


def _as_aware(value: datetime) -> datetime:
    """SQLite round-trips timestamps as naive UTC; compare in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _job_duration_histogram_lines(session: Session) -> list[str]:
    """Render the G8 job-duration histogram from completed Job rows.

    Like every metric here it is derived from the database at scrape time, so
    the cumulative bucket/sum/count cover the retained history (30-day job
    retention) instead of one API process lifetime. Jobs without both
    lifecycle timestamps (never started or never finished) contribute nothing.
    """
    durations: list[float] = []
    rows = session.query(Job.started_at, Job.finished_at).filter(
        Job.started_at.is_not(None), Job.finished_at.is_not(None)
    )
    for started_at, finished_at in rows:
        if started_at is None or finished_at is None:  # narrowed by the filter
            continue
        elapsed = (_as_aware(finished_at) - _as_aware(started_at)).total_seconds()
        durations.append(max(0.0, elapsed))
    durations.sort()
    total = round(sum(durations), 6)
    lines = [
        "# HELP hub_job_duration_seconds Job wall-clock duration from started_at to finished_at.",
        "# TYPE hub_job_duration_seconds histogram",
    ]
    for bucket in _JOB_DURATION_BUCKETS_SECONDS:
        lines.append(
            f'hub_job_duration_seconds_bucket{{le="{bucket:g}"}} '
            f"{bisect_right(durations, bucket)}"
        )
    lines.append(f'hub_job_duration_seconds_bucket{{le="+Inf"}} {len(durations)}')
    lines.append(f"hub_job_duration_seconds_sum {total}")
    lines.append(f"hub_job_duration_seconds_count {len(durations)}")
    return lines
