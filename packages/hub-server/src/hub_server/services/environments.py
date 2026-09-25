"""Read-only scanning of installed conda environment material.

The Hub's ``environments/`` directory sits in the backup blind spot (backups
exclude it, see ``services/backup.py``), so material can outlive its records
after a restore or an out-of-band copy. This module reports that state; it
never deletes anything. Following the R4 ruling of review doc
插件系统成熟度-审查方案-2026-09-25.md, the PluginBuild table is the only
source of truth for "referenced": a directory that matches no Build row is a
candidate for cleanup, never judged useless by name alone, and the guarded
deletion that acts on these reports is owned by B4b, not here.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.models import Job, PluginBuild
from hub_server.services.audit import record as audit

_LOGGER = logging.getLogger(__name__)

# Runner staging directories: the conda executor unpacks an archive into
# "{build_key}-{uuid4().hex}.tmp" below environments/ before promoting it to
# "{build_key}" (hub_runner.conda_executor). uuid4().hex is 32 lowercase hex
# characters, so the staging suffix is unambiguous.
_TMP_STAGING_PATTERN = re.compile(r"^(?P<build_key>.+)-[0-9a-f]{32}\.tmp$")

# A directory with an unknown prefix that appeared within this window is
# exempted (and counted) instead of reported: a crash between directory
# creation and Build-row visibility must not be flagged as an orphan.
_YOUNG_UNKNOWN_GRACE_MINUTES = 60

# Job statuses that keep a conda environment actively in use.
_ACTIVE_JOB_STATUSES = ("PENDING", "PREPARING", "RUNNING")


def scan_environment_orphans(
    session: Session,
    storage_root: Path,
    *,
    now: datetime | None = None,
) -> int:
    """Report first-level ``environments/`` directories that reference no Build.

    A directory is an orphan when its name is neither any PluginBuild.build_key
    nor a ``{build_key}-*.tmp`` staging directory of one. Orphans are ONLY
    reported — one ``environments_orphans_detected`` audit entry (when any are
    found) plus a WARNING log — and never deleted or modified. Returns the
    number of orphan directories so the hosting sweep can log it like any other
    task result.

    Exemptions, most specific first:

    - the directory name (or the staging prefix) matches a Build row — the
      PluginBuild table is the reference source, per the R4 ruling;
    - the name is referenced by an active conda Job (status PENDING, PREPARING,
      or RUNNING) — defence in depth so a stricter Build-state rule can never
      strand the environment of a running or queued Job;
    - the directory has an unknown prefix but is younger than
      ``_YOUNG_UNKNOWN_GRACE_MINUTES`` — exempted and counted in the audit
      detail instead of reported.
    """
    environments_root = storage_root / "environments"
    try:
        entries = [entry for entry in environments_root.iterdir() if entry.is_dir()]
    except OSError:
        return 0
    if not entries:
        return 0

    known_build_keys = set(session.scalars(select(PluginBuild.build_key)).all())
    active_build_keys = set(
        session.scalars(
            select(PluginBuild.build_key)
            .join(Job, Job.plugin_build_id == PluginBuild.id)
            .where(
                Job.runtime_type == "conda-pack",
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
        ).all()
    )
    current = now if now is not None else datetime.now(UTC)
    young_cutoff = current - timedelta(minutes=_YOUNG_UNKNOWN_GRACE_MINUTES)

    orphans: list[str] = []
    tmp_exempt = 0
    young_exempt = 0
    for entry in entries:
        name = entry.name
        if name in known_build_keys or name in active_build_keys:
            continue
        staging = _TMP_STAGING_PATTERN.fullmatch(name)
        if staging is not None and staging.group("build_key") in (
            known_build_keys | active_build_keys
        ):
            tmp_exempt += 1
            continue
        try:
            modified_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
        except OSError:
            modified_at = None
        if modified_at is not None and modified_at >= young_cutoff:
            young_exempt += 1
            continue
        orphans.append(name)

    if orphans:
        audit(
            session,
            actor_type="system",
            actor_id=None,
            actor_name="cleaner",
            action="environments_orphans_detected",
            resource_type="environment",
            detail={
                "count": len(orphans),
                "directories": sorted(orphans),
                "exempt_tmp_staging": tmp_exempt,
                "exempt_young_unknown": young_exempt,
                "disposition": "report_only",
            },
        )
        session.commit()
        _LOGGER.warning(
            "environments 孤儿目录扫描发现 %d 个无 Build 引用的目录: %s"
            " (tmp 豁免 %d, 新建豁免 %d); 仅报告不删除, 删除能力属 B4b",
            len(orphans),
            sorted(orphans),
            tmp_exempt,
            young_exempt,
        )
    return len(orphans)
