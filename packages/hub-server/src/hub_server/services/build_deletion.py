"""Guarded deletion of deprecated Plugin Builds (B4b, review doc G4(2)).

Deletion is the irreversible half of the retirement story started by B4a's
``deprecate``: a DEPRECATED Build keeps its storage, environment directory, and
docker image until this service removes them. Because the operation cannot be
undone, every path is guarded and rehearsed first:

Guards (review ruling R2, evaluated against *current* table state, any hit
rejects the whole request with 409):

1. The Build must be DEPRECATED. READY (a disabled Build), ENABLED, INSTALLING,
   and FAILED Builds are all rejected — deprecation is the mandatory cooling-off
   step that already stopped new Jobs from resolving this Build.
2. Enabled Schedule references. Schedules resolve by (plugin_id, version,
   runtime_type) — not by build id — so the question is whether the
   (plugin, version) would still have an ENABLED Build able to serve the
   schedule. If an enabled Schedule names the same (plugin_id, version,
   runtime_type) and the Build being deleted is the last one for that triple,
   deletion is rejected until the schedule is disabled or re-pointed.
3. Pipeline step references. Any Pipeline whose ``steps_json`` names the same
   (plugin_id, version, runtime_type) rejects deletion until the pipeline is
   edited.
4. Active Jobs (PENDING / PREPARING / RUNNING / CANCEL_REQUESTED) referencing
   the Build reject deletion.

Job retention weakening (write-down required by the review doc): the retention
sweeper deletes terminal Job rows after ``job_retention_days`` — 30 days when
the plan was finalized, shortened to 7 days by the 2026-09-29 disk incident —
so the guard must not (and cannot) depend on Job history: a Build that served
Jobs keeps no permanent count of them. The guard therefore reads current table
state only, and the deletion audit entry records the moment and the number of
terminal Job rows that were still present and removed with the Build. Historical
Job rows that retention already removed are simply gone; nothing about them is
deleted here.

Deletion order (review ruling R3, not to be reordered):

1. Storage directories (``plugins/<build_key>/`` — the sole restore source of
   the docker image archive, and ``environments/<build_key>`` when present) and
   the database rows (Environment, RunnerOperation, terminal Job rows with
   their JobFile/JobCallback children and OUTPUT FileRecords, then the
   PluginBuild row itself). Both are RESTRICT-referencing children: with
   ``PRAGMA foreign_keys=ON`` the Build row cannot go while they exist. Removing
   them eliminates every reference the registry holds before the image is
   touched.
2. The docker image, removed precisely as ``docker image rm sha256:<digest>``
   (docker-py ``images.remove`` with the full digest reference) — only for
   docker Builds with a stored digest, never any form of ``prune``. A failed
   image removal is audited as a warning and the deletion continues; the
   residue is reconcilable later by the G4c image-presence checker because no
   registry reference survives step 1.

Recovery paths, stated plainly: deleting the Build row frees the immutable
``(version, platform, runtime)`` slot, so re-installing the exact same version
succeeds again after deletion (before deletion the uniqueness constraint
rejects it with 409 ``PLUGIN_BUILD_ALREADY_EXISTS`` — that is the B4a
deprecate-only path). What deletion does NOT restore is the material: storage
directories, the docker image, and past Job workspaces/outputs are gone.
Registry留档 (recording build_key, package sha256, and image digest before
deleting) or a fresh package upload is the only way back; the first real
deletion should be rehearsed with ``dry_run=true``.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hub_server.dependencies_auth import Actor
from hub_server.errors import HubError
from hub_server.models import (
    Environment,
    FileRecord,
    Job,
    JobCallback,
    JobFile,
    Pipeline,
    Plugin,
    PluginBuild,
    PluginVersion,
    RunnerOperation,
    Schedule,
)
from hub_server.services.audit import record as audit
from hub_server.services.quotas import ACTIVE_JOB_STATUSES
from hub_server.services.retention import TERMINAL_JOB_STATUSES
from hub_server.storage import LocalStorage

_LOGGER = logging.getLogger(__name__)

# environment.image_digest holds the bare sha256 hex (the runner normalizes the
# "sha256:"-prefixed form away, see hub_runner.docker_executor._normalize_digest
# and runner_operations._validate_success_metadata). Only a full 64-hex digest
# is exact enough to remove one specific image; anything else is reported and
# left in place rather than guessed at.
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

DELETION_AUDIT_ACTION = "plugin_build.delete"


class ImageRemovalError(RuntimeError):
    """The docker daemon refused (or failed) to remove one exact image."""


class ImageRemover(Protocol):
    """Removes exactly one image reference from the local docker daemon."""

    def remove(self, image_ref: str) -> None:
        """Remove ``image_ref`` (``sha256:<64-hex>``); raise on failure.

        An already-absent image is success: the goal state is achieved.
        """
        ...


class DockerImageRemover:
    """docker-py-backed ImageRemover; the SDK is imported lazily per call."""

    def remove(self, image_ref: str) -> None:
        import docker  # type: ignore[import-untyped]

        try:
            docker.from_env().images.remove(image_ref)
        except Exception as error:
            from docker.errors import ImageNotFound  # type: ignore[import-untyped]

            if isinstance(error, ImageNotFound):
                return
            raise ImageRemovalError(
                str(error) or error.__class__.__name__
            ) from error


@dataclass(frozen=True, slots=True)
class GuardViolation:
    """One guard hit that rejects the deletion, with its stable error code."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    """The natural key and display fields of one Build, captured before deletion."""

    build_key: str
    plugin_id: str
    version: str
    runtime_type: str
    status: str


@dataclass(frozen=True, slots=True)
class DeletionPlan:
    """What a deletion would do, plus the guard evaluation behind it."""

    identity: BuildIdentity
    allowed: bool
    violations: list[GuardViolation] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    image_digest: str | None = None
    image_ref: str | None = None


class PluginBuildDeletionService:
    """Evaluate, rehearse, and execute guarded Plugin Build deletions."""

    def __init__(
        self, session: Session, storage: LocalStorage, *, image_remover: ImageRemover
    ) -> None:
        self._session = session
        self._storage = storage
        self._image_remover = image_remover

    # ------------------------------------------------------------------ plan

    def plan(self, build: PluginBuild) -> DeletionPlan:
        """Evaluate guards and collect the action list without changing anything."""
        identity = self._identity(build)
        violations = self._evaluate_guards(build, identity)
        image_digest, image_ref = self._image_target(build)
        return DeletionPlan(
            identity=identity,
            allowed=not violations,
            violations=violations,
            actions=self._planned_actions(build, identity, image_ref),
            image_digest=image_digest,
            image_ref=image_ref,
        )

    # ---------------------------------------------------------------- delete

    def delete(self, build: PluginBuild, *, actor: Actor, ip: str | None) -> None:
        """Run the guarded deletion in the R3 order; raise 409 on any guard hit."""
        identity = self._identity(build)
        violations = self._evaluate_guards(build, identity)
        if violations:
            raise HubError(
                code=violations[0].code,
                message=violations[0].message,
                status_code=409,
                details={"violations": [
                    {"code": violation.code, "message": violation.message}
                    for violation in violations
                ]},
            )

        image_digest, image_ref = self._image_target(build)
        jobs = self._terminal_jobs(build)
        operations_deleted = self._count(
            select(func.count()).select_from(RunnerOperation).where(
                RunnerOperation.plugin_build_id == build.id
            )
        )
        storage_removed = self._remove_storage(identity.build_key)
        jobs_deleted = self._delete_database_rows(build, jobs)

        image_removed: bool | None = None
        image_error: str | None = None
        if image_ref is not None:
            try:
                self._image_remover.remove(image_ref)
                image_removed = True
            except ImageRemovalError as error:
                # R3: the registry references are already gone, so the residue is
                # a lone image the G4c presence checker can reconcile later.
                # Continue; never compensate with a prune.
                image_removed = False
                image_error = str(error)
                _LOGGER.warning(
                    "插件 Build %s 删除后镜像 %s 清理失败: %s",
                    identity.build_key,
                    image_ref,
                    error,
                )

        audit(
            self._session,
            actor_type=actor.kind,
            actor_id=actor.id,
            actor_name=actor.name,
            action=DELETION_AUDIT_ACTION,
            resource_type="plugin_build",
            resource_id=identity.build_key,
            detail={
                "build_key": identity.build_key,
                "plugin_id": identity.plugin_id,
                "version": identity.version,
                "runtime_type": identity.runtime_type,
                "image_digest": image_digest,
                "image_ref": image_ref,
                "image_removed": image_removed,
                "image_error": image_error,
                "jobs_deleted": jobs_deleted,
                "operations_deleted": operations_deleted,
                "storage_removed": storage_removed,
            },
            ip=ip,
        )
        self._session.commit()

    # ---------------------------------------------------------------- guards

    def _evaluate_guards(
        self, build: PluginBuild, identity: BuildIdentity
    ) -> list[GuardViolation]:
        violations: list[GuardViolation] = []
        if build.status != "DEPRECATED":
            violations.append(
                GuardViolation(
                    code="PLUGIN_BUILD_NOT_DEPRECATED",
                    message=(
                        "仅 DEPRECATED 状态的构建可删除, 请先执行 "
                        "POST /plugin-builds/{build_key}/deprecate"
                    ),
                )
            )
        self._guard_enabled_schedules(build, identity, violations)
        self._guard_pipeline_steps(identity, violations)
        self._guard_active_jobs(build, violations)
        return violations

    def _guard_enabled_schedules(
        self,
        build: PluginBuild,
        identity: BuildIdentity,
        violations: list[GuardViolation],
    ) -> None:
        """Reject when deleting the last Build of a schedule-referenced triple.

        Schedules resolve (plugin_id, version, runtime_type) to the ENABLED
        Build at trigger time, never to a build id. A DEPRECATED Build serves
        nothing already, but if no sibling Build of the same triple remains
        after this deletion, an enabled Schedule can never fire again until a
        package is re-installed — that decision belongs to the operator, so it
        blocks here.
        """
        referencing = self._session.scalars(
            select(Schedule).where(
                Schedule.enabled.is_(True),
                Schedule.plugin_id == identity.plugin_id,
                Schedule.version == identity.version,
                Schedule.runtime_type == identity.runtime_type,
            )
        ).all()
        if not referencing:
            return
        siblings = self._session.scalar(
            select(func.count())
            .select_from(PluginBuild)
            .join(PluginVersion, PluginVersion.id == PluginBuild.plugin_version_id)
            .join(Plugin, Plugin.id == PluginVersion.plugin_id)
            .where(
                Plugin.plugin_key == identity.plugin_id,
                PluginVersion.version == identity.version,
                PluginBuild.runtime_type == identity.runtime_type,
                PluginBuild.id != build.id,
            )
        )
        if siblings:
            return
        names = ", ".join(sorted(schedule.name for schedule in referencing))
        violations.append(
            GuardViolation(
                code="PLUGIN_BUILD_SCHEDULE_REFERENCED",
                message=(
                    f"存在启用中的调度引用该构建 ({identity.plugin_id} "
                    f"{identity.version} {identity.runtime_type}): {names}, "
                    "请先停用或改指向这些调度"
                ),
            )
        )

    def _guard_pipeline_steps(
        self, identity: BuildIdentity, violations: list[GuardViolation]
    ) -> None:
        """Reject when any Pipeline step references the same (plugin, version, runtime)."""
        referencing: list[str] = []
        for pipeline in self._session.scalars(select(Pipeline).order_by(Pipeline.id)).all():
            if _pipeline_references(pipeline.steps_json, identity):
                referencing.append(pipeline.name)
        if referencing:
            violations.append(
                GuardViolation(
                    code="PLUGIN_BUILD_PIPELINE_REFERENCED",
                    message=(
                        "存在流水线步骤引用该构建: "
                        f"{', '.join(referencing)}, 请先修改这些流水线"
                    ),
                )
            )

    def _guard_active_jobs(
        self, build: PluginBuild, violations: list[GuardViolation]
    ) -> None:
        """Reject while a PENDING/PREPARING/RUNNING/CANCEL_REQUESTED Job exists.

        Retention keeps erasing terminal Job rows, so this guard reads the
        current table only: the active states below are the ones that can
        still transition.
        """
        active = self._count(
            select(func.count())
            .select_from(Job)
            .where(
                Job.plugin_build_id == build.id,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
        )
        if active:
            violations.append(
                GuardViolation(
                    code="PLUGIN_BUILD_IN_USE",
                    message=f"存在活跃 Job (PENDING/PREPARING/RUNNING/"
                    f"CANCEL_REQUESTED) 引用该构建: {active} 个",
                )
            )

    # -------------------------------------------------------------- deletion

    def _remove_storage(self, build_key: str) -> list[str]:
        """Remove the Build storage and conda environment directory; return what went."""
        removed: list[str] = []
        plugin_directory = self._storage.open_relative(f"plugins/{build_key}")
        if plugin_directory.exists():
            self._storage.remove_plugin_build(build_key)
            removed.append(f"plugins/{build_key}")
        environment_directory = self._storage.open_relative(f"environments/{build_key}")
        if environment_directory.exists():
            self._storage.remove_environment(build_key)
            removed.append(f"environments/{build_key}")
        return removed

    def _delete_database_rows(
        self, build: PluginBuild, jobs: list[tuple[int, str, str | None]]
    ) -> int:
        """Delete RESTRICT-referencing children then the Build row; commit once.

        The order mirrors the retention sweeper for the terminal Job rows
        (JobFile and JobCallback first, then the Job, then the OUTPUT
        FileRecords) so the RESTRICT guards never fire mid-transaction.
        Workspaces and output payloads are removed from disk alongside, exactly
        as retention would have done — leaving them would strand files no
        sweeper can ever find again, because their Job rows are gone.
        """
        storage_root = self._storage.root
        for job_id, _, workspace_path in jobs:
            outputs = self._session.query(FileRecord.id, FileRecord.relative_path).join(
                JobFile, JobFile.file_record_id == FileRecord.id
            ).filter(JobFile.job_id == job_id, JobFile.role == "OUTPUT").all()
            if workspace_path:
                shutil.rmtree(storage_root / workspace_path, ignore_errors=True)
            self._session.query(JobFile).filter(JobFile.job_id == job_id).delete(
                synchronize_session=False
            )
            self._session.query(JobCallback).filter(JobCallback.job_id == job_id).delete(
                synchronize_session=False
            )
            self._session.query(Job).filter(Job.id == job_id).delete(
                synchronize_session=False
            )
            for _, relative_path in outputs:
                with _suppressed_os_error():
                    (storage_root / relative_path).unlink()
            if outputs:
                self._session.query(FileRecord).filter(
                    FileRecord.id.in_([record_id for record_id, _ in outputs])
                ).delete(synchronize_session=False)
        self._session.query(Environment).filter(
            Environment.plugin_build_id == build.id
        ).delete(synchronize_session=False)
        self._session.query(RunnerOperation).filter(
            RunnerOperation.plugin_build_id == build.id
        ).delete(synchronize_session=False)
        self._session.query(PluginBuild).filter(PluginBuild.id == build.id).delete(
            synchronize_session=False
        )
        self._session.expire_all()
        self._session.commit()
        return len(jobs)

    def _terminal_jobs(self, build: PluginBuild) -> list[tuple[int, str, str | None]]:
        """Terminal Job rows still referencing the Build (deleted along with it)."""
        return list(
            self._session.query(Job.id, Job.job_key, Job.workspace_path)
            .filter(
                Job.plugin_build_id == build.id,
                Job.status.in_(TERMINAL_JOB_STATUSES),
            )
            .all()
        )

    # --------------------------------------------------------------- helpers

    def _planned_actions(
        self,
        build: PluginBuild,
        identity: BuildIdentity,
        image_ref: str | None,
    ) -> list[str]:
        """Human-readable action list mirroring what ``delete`` executes, in order."""
        actions = [f"storage: plugins/{identity.build_key}"]
        if self._storage.open_relative(f"environments/{identity.build_key}").exists():
            actions.append(f"storage: environments/{identity.build_key}")
        actions.append(f"db: plugin_builds 行 {identity.build_key}")
        if build.environment is not None:
            actions.append("db: environments 行")
        operations = self._count(
            select(func.count()).select_from(RunnerOperation).where(
                RunnerOperation.plugin_build_id == build.id
            )
        )
        if operations:
            actions.append(f"db: runner_operations 行 x{operations}")
        jobs = len(self._terminal_jobs(build))
        if jobs:
            actions.append(f"db: 终态 Job 行 x{jobs} (连同工作区与输出文件)")
        if image_ref is not None:
            actions.append(f"image: docker image rm {image_ref}")
        return actions

    def _image_target(self, build: PluginBuild) -> tuple[str | None, str | None]:
        """Return (digest, exact reference) when a docker image removal applies."""
        if build.runtime_type != "docker":
            return None, None
        digest = build.environment.image_digest if build.environment else None
        if digest is None or _DIGEST_PATTERN.fullmatch(digest) is None:
            return digest, None
        return digest, f"sha256:{digest}"

    def _identity(self, build: PluginBuild) -> BuildIdentity:
        return BuildIdentity(
            build_key=build.build_key,
            plugin_id=build.plugin_version.plugin.plugin_key,
            version=build.plugin_version.version,
            runtime_type=build.runtime_type,
            status=build.status,
        )

    def _count(self, query: Any) -> int:
        return int(self._session.scalar(query) or 0)


def _pipeline_references(steps_json: object, identity: BuildIdentity) -> bool:
    """Whether any pipeline step names the Build's (plugin_id, version, runtime).

    Steps are plain JSON dicts (see services.pipelines._step_fields). A step
    without ``runtime_type`` is already unable to run, but it still names the
    plugin version, so it is treated as referencing every runtime — the
    conservative reading sends the operator to fix the pipeline.
    """
    if not isinstance(steps_json, list):
        return False
    for step in steps_json:
        if not isinstance(step, dict):
            continue
        if (
            step.get("plugin_id") == identity.plugin_id
            and step.get("version") == identity.version
            and step.get("runtime_type") in (None, identity.runtime_type)
        ):
            return True
    return False


class _suppressed_os_error:
    """Local stand-in for contextlib.suppress(OSError) to keep imports flat."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exception: object) -> bool:
        return isinstance(exception[1], OSError)
