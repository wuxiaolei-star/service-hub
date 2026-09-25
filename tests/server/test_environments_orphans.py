"""Environments orphan scan: reference-aware reporting without any deletion."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from hub_server.models import AuditLogRecord, Job, Plugin, PluginBuild, PluginVersion
from hub_server.services.environments import scan_environment_orphans
from sqlalchemy.orm import Session

_ACTION = "environments_orphans_detected"


def test_directory_without_build_reference_is_reported(
    session: Session, tmp_path: Path
) -> None:
    """A directory matching no Build row is the orphan the scan exists to find."""
    _add_build(session)
    _make_environment_dir(tmp_path, "plugin_build_orphan", age_minutes=120)

    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 1
    entry = session.query(AuditLogRecord).filter_by(action=_ACTION).one()
    assert entry.actor_name == "cleaner"
    assert entry.detail is not None
    assert entry.detail["count"] == 1
    assert entry.detail["directories"] == ["plugin_build_orphan"]
    assert entry.detail["disposition"] == "report_only"
    # Report-only: the scan never deletes or touches the reported material.
    assert (tmp_path / "environments" / "plugin_build_orphan").is_dir()


def test_build_directory_and_its_staging_are_exempt(session: Session, tmp_path: Path) -> None:
    """Directories named by a Build row (and its ``{key}-*.tmp`` staging) stay clear."""
    build = _add_build(session)
    staging = f"{build.build_key}-{uuid4().hex}.tmp"
    _make_environment_dir(tmp_path, build.build_key, age_minutes=120)
    _make_environment_dir(tmp_path, staging, age_minutes=120)

    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 0
    assert session.query(AuditLogRecord).filter_by(action=_ACTION).count() == 0


def test_active_conda_job_keeps_environment_exempt(session: Session, tmp_path: Path) -> None:
    """A queued or running conda Job keeps its Build's environment alive (R4).

    This is defence in depth on top of the Build-row rule: even if a stricter
    Build-state rule ever narrows the name exemption, an active Job must never
    let its environment be reported as orphan material.
    """
    build = _add_build(session, status="DEPRECATED")
    session.add(
        Job(
            plugin_build=build,
            runtime_type="conda-pack",
            runtime_fingerprint="c" * 64,
            status="PENDING",
            params_json={},
            inputs_json={},
            timeout_seconds=30,
        )
    )
    session.commit()
    _make_environment_dir(tmp_path, build.build_key, age_minutes=120)

    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 0


def test_young_unknown_directory_is_exempted_and_counted(
    session: Session, tmp_path: Path
) -> None:
    """Fresh unknown prefixes are grace-period exempt, but still counted."""
    _add_build(session)
    _make_environment_dir(tmp_path, "plugin_build_orphan", age_minutes=120)
    _make_environment_dir(tmp_path, "dev-scratch", age_minutes=5)

    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 1
    entry = session.query(AuditLogRecord).filter_by(action=_ACTION).one()
    assert entry.detail is not None
    assert entry.detail["exempt_young_unknown"] == 1


def test_staging_shape_without_a_build_reference_is_reported(
    session: Session, tmp_path: Path
) -> None:
    """A ``*-<uuid>.tmp`` name alone is not a reference: only Build rows are."""
    staging = f"untracked-{uuid4().hex}.tmp"
    _make_environment_dir(tmp_path, staging, age_minutes=120)

    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 1
    entry = session.query(AuditLogRecord).filter_by(action=_ACTION).one()
    assert entry.detail is not None
    assert entry.detail["directories"] == [staging]


def test_missing_environments_root_reports_nothing(session: Session, tmp_path: Path) -> None:
    orphans = scan_environment_orphans(session, tmp_path)

    assert orphans == 0


def _make_environment_dir(storage_root: Path, name: str, *, age_minutes: int) -> Path:
    directory = storage_root / "environments" / name
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now(UTC) - timedelta(minutes=age_minutes)).timestamp()
    os.utime(directory, (stamp, stamp))
    return directory


def _add_build(session: Session, *, status: str = "ENABLED") -> PluginBuild:
    build = PluginBuild(
        plugin_version=PluginVersion(
            plugin=Plugin(plugin_key=f"plugin_{uuid4().hex[:12]}", name="Orphan Scan Plugin"),
            version="1.0.0",
            spec_version="1.0",
            sdk_version="1.0",
            source_sha256="a" * 64,
            manifest_json={"plugin": {"id": "orphan_scan", "version": "1.0.0"}},
            status="INSTALLED",
        ),
        manifest_build_id="package-build-orphan-scan",
        target_os="linux",
        target_arch="amd64",
        runtime_type="conda-pack",
        python_version="3.12",
        sdk_version="1.0.0",
        package_sha256="d" * 64,
        source_sha256="a" * 64,
        runtime_archive_path="runtime/env.tar.zst",
        runtime_fingerprint="d" * 64,
        build_metadata_json={"runtime": {"type": "conda-pack"}},
        status=status,
    )
    session.add(build)
    session.commit()
    return build
