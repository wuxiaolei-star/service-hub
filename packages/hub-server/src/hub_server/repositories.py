"""Transactional persistence operations shared by Hub services."""

from datetime import UTC, datetime
from typing import Any, Final, cast

from python_hub_contracts import JobStatus, PluginBuildManifest, PluginManifest, RuntimeType
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from hub_server.models import (
    Environment,
    Job,
    Plugin,
    PluginBuild,
    PluginVersion,
    RunnerOperation,
    _public_id,
)

_TERMINAL_JOB_STATUSES: Final = frozenset(
    {
        JobStatus.SUCCESS,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMED_OUT,
    }
)
_ALLOWED_JOB_TRANSITIONS: Final = {
    JobStatus.PENDING: frozenset({JobStatus.PREPARING}),
    JobStatus.PREPARING: frozenset(
        {
            JobStatus.RUNNING,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.TIMED_OUT,
        }
    ),
    JobStatus.RUNNING: _TERMINAL_JOB_STATUSES,
}


class HubRepository:
    """Own atomic creation, claiming, and lifecycle transitions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_build_installation(
        self,
        *,
        plugin_manifest: PluginManifest,
        build_manifest: PluginBuildManifest,
        package_sha256: str,
        package_path: str | None = None,
    ) -> PluginBuild:
        """Persist an immutable build, its environment, and one pending install operation."""
        build_manifest.assert_matches_plugin(plugin_manifest)
        plugin_snapshot = plugin_manifest.model_dump(mode="json")
        build_snapshot = build_manifest.model_dump(mode="json")
        plugin = self._session.scalar(
            select(Plugin).where(Plugin.plugin_key == plugin_manifest.plugin.id)
        )
        if plugin is None:
            plugin = Plugin(
                plugin_key=plugin_manifest.plugin.id,
                name=plugin_manifest.plugin.name,
                description=plugin_manifest.plugin.description,
                category=plugin_manifest.plugin.category,
                author=plugin_manifest.plugin.author,
            )
            self._session.add(plugin)

        version = self._session.scalar(
            select(PluginVersion).where(
                PluginVersion.plugin == plugin,
                PluginVersion.version == plugin_manifest.plugin.version,
            )
        )
        if version is None:
            version = PluginVersion(
                plugin=plugin,
                version=plugin_manifest.plugin.version,
                spec_version=plugin_manifest.spec_version,
                sdk_version=plugin_manifest.sdk.version,
                source_sha256=build_manifest.source_sha256,
                manifest_json=plugin_snapshot,
                status="INSTALLED",
            )
            self._session.add(version)
        elif (
            version.source_sha256 != build_manifest.source_sha256
            or version.manifest_json != plugin_snapshot
        ):
            raise ValueError("plugin version source and manifest are immutable")

        if build_manifest.runtime.type == "conda-pack":
            fingerprint = build_manifest.runtime.fingerprint
            image_digest = None
        else:
            fingerprint = build_manifest.runtime.digest
            image_digest = build_manifest.runtime.digest

        build_key = _public_id("plugin_build")
        build = PluginBuild(
            build_key=build_key,
            manifest_build_id=build_manifest.build_id,
            plugin_version=version,
            target_os=build_manifest.target.os,
            target_arch=build_manifest.target.arch,
            runtime_type=build_manifest.runtime.type,
            python_version=build_manifest.python_version,
            sdk_version=build_manifest.sdk_version,
            package_path=package_path,
            package_sha256=package_sha256,
            source_sha256=build_manifest.source_sha256,
            runtime_archive_path=build_manifest.runtime.archive,
            runtime_fingerprint=fingerprint,
            build_metadata_json=build_snapshot,
            status="INSTALLING",
        )
        environment = Environment(
            plugin_build=build,
            runtime_type=build_manifest.runtime.type,
            fingerprint=fingerprint,
            image_digest=image_digest,
            metadata_json=build_manifest.runtime.model_dump(mode="json"),
            status="INSTALLING",
        )
        operation = RunnerOperation(
            operation_key=_public_id("operation"),
            plugin_build=build,
            runtime_type=build_manifest.runtime.type,
            kind="INSTALL",
            status="PENDING",
            payload_json={
                "build_id": build_key,
                "manifest_build_id": build_manifest.build_id,
                "runtime": build_manifest.runtime.model_dump(mode="json"),
            },
            timeout_seconds=plugin_manifest.execution.timeout,
        )
        self._session.add_all([build, environment, operation])
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return build

    def claim_operation(self, runtime_type: RuntimeType) -> RunnerOperation | None:
        """Atomically claim the oldest pending installation for one runtime."""
        operation = self._session.scalar(
            select(RunnerOperation)
            .where(
                RunnerOperation.runtime_type == runtime_type,
                RunnerOperation.status == "PENDING",
            )
            .order_by(RunnerOperation.created_at, RunnerOperation.id)
            .limit(1)
        )
        if operation is None:
            self._session.commit()
            return None
        now = datetime.now(UTC)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(RunnerOperation)
                .where(
                    RunnerOperation.id == operation.id,
                    RunnerOperation.status == "PENDING",
                )
                .values(status="PREPARING", claimed_at=now, updated_at=now)
            )
        )
        if result.rowcount != 1:
            self._session.rollback()
            return None
        self._session.refresh(operation)
        self._session.commit()
        return operation

    def claim_job(self, runtime_type: RuntimeType) -> Job | None:
        """Atomically claim the oldest pending Job for one runtime."""
        job = self._session.scalar(
            select(Job)
            .where(Job.runtime_type == runtime_type, Job.status == JobStatus.PENDING)
            .order_by(Job.created_at, Job.id)
            .limit(1)
        )
        if job is None:
            self._session.commit()
            return None
        now = datetime.now(UTC)
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == JobStatus.PENDING)
                .values(status=JobStatus.PREPARING, claimed_at=now, updated_at=now)
            ),
        )
        if result.rowcount != 1:
            self._session.rollback()
            return None
        self._session.refresh(job)
        self._session.commit()
        return job

    def transition_job(
        self,
        job: Job | str,
        status: JobStatus,
        *,
        expected_status: JobStatus | None = None,
        error_summary: str | None = None,
        exit_code: int | None = None,
    ) -> Job:
        """Atomically move a Job through the shared lifecycle state machine."""
        record = (
            job
            if isinstance(job, Job)
            else self._session.scalar(select(Job).where(Job.job_key == job))
        )
        if record is None:
            raise ValueError("job not found")
        current_status = JobStatus(record.status)
        if current_status in _TERMINAL_JOB_STATUSES:
            raise ValueError("terminal jobs cannot transition")
        if expected_status is not None and current_status is not expected_status:
            raise ValueError(
                "job state conflict: "
                f"expected {expected_status.value}, found {current_status.value}"
            )
        if status not in _ALLOWED_JOB_TRANSITIONS[current_status]:
            raise ValueError(f"invalid job transition: {current_status.value} -> {status.value}")

        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": status.value,
            "updated_at": now,
            "error_summary": error_summary,
            "exit_code": exit_code,
        }
        if status is JobStatus.RUNNING:
            values["started_at"] = now
        if status in _TERMINAL_JOB_STATUSES:
            values["finished_at"] = now
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Job)
                .where(Job.id == record.id, Job.status == current_status.value)
                .values(**values)
            ),
        )
        if result.rowcount != 1:
            self._session.rollback()
            raise ValueError("job state changed concurrently")
        self._session.refresh(record)
        self._session.commit()
        return record
