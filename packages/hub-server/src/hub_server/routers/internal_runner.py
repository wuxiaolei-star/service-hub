"""Private API used by isolated Conda and Docker runner services."""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from python_hub_contracts import (
    JobRuntimeSpec,
    JobStatus,
    LogEvent,
    LogLevel,
    PluginBuildManifest,
    PluginManifest,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.errors import HubError
from hub_server.models import Job, PluginBuild
from hub_server.repositories import HubRepository
from hub_server.schemas import (
    RunnerBuildClaim,
    RunnerCancellationResponse,
    RunnerClaimRequest,
    RunnerCompletionResponse,
    RunnerEventAcceptedResponse,
    RunnerJobClaimResponse,
    RunnerJobCompleteRequest,
    RunnerJobEventRequest,
    RunnerJobPaths,
    RunnerOperationClaimResponse,
    RunnerOperationCompleteRequest,
    RunnerReconcileResponse,
)
from hub_server.services.failures import (
    PLUGIN_OUTPUT_MISSING,
    PLUGIN_OUTPUT_REGISTER_FAILED,
    failure_summary,
)
from hub_server.services.job_events import event_log_relative_path
from hub_server.services.runner_operations import RunnerOperationService
from hub_server.services.workspaces import JobWorkspaceService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(tags=["internal-runner"], include_in_schema=False)


def _authorize_runner(
    settings: Annotated[HubSettings, Depends(get_settings)],
    token: Annotated[str | None, Header(alias="X-Hub-Runner-Token")] = None,
) -> None:
    expected = settings.runner.shared_token.get_secret_value()
    if token is None or not hmac.compare_digest(token, expected):
        raise HubError(
            code="RUNNER_AUTH_FAILED",
            message="Runner 身份验证失败",
            status_code=403,
        )


RunnerAuthorization = Annotated[None, Depends(_authorize_runner)]


@router.post("/operations/claim", response_model=RunnerOperationClaimResponse | None)
def claim_operation(
    request: RunnerClaimRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
    response: Response,
) -> RunnerOperationClaimResponse | None:
    operation = HubRepository(session).claim_operation(request.runtime_type)
    if operation is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    return RunnerOperationClaimResponse(
        operation_id=operation.operation_key,
        runtime_type=request.runtime_type,
        kind="INSTALL",
        timeout_seconds=operation.timeout_seconds,
        build=_build_claim(operation.plugin_build, settings),
    )


@router.post(
    "/operations/{operation_key}/complete", response_model=RunnerCompletionResponse
)
def complete_operation(
    operation_key: str,
    request: RunnerOperationCompleteRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
) -> RunnerCompletionResponse:
    operation = RunnerOperationService(session).complete(
        operation_key,
        runtime_type=request.runtime_type,
        status=request.status,
        environment_path=request.environment_path,
        image_digest=request.image_digest,
        metadata=request.metadata,
        error_summary=request.error_summary,
        exit_code=request.exit_code,
    )
    return RunnerCompletionResponse(id=operation.operation_key, status=request.status)


@router.post("/jobs/claim", response_model=RunnerJobClaimResponse | None)
def claim_job(
    request: RunnerClaimRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
    response: Response,
) -> RunnerJobClaimResponse | None:
    job = HubRepository(session).claim_job(request.runtime_type)
    if job is None:
        response.status_code = status.HTTP_204_NO_CONTENT
        return None
    if job.job_json is None or job.workspace_path is None:
        raise HubError(
            code="RUNNER_JOB_INVALID",
            message="Job 工作区尚未准备完成",
            status_code=409,
        )
    return RunnerJobClaimResponse(
        job_id=job.job_key,
        runtime_type=request.runtime_type,
        cancel_requested=job.cancel_requested,
        workspace=job.workspace_path,
        paths=RunnerJobPaths(),
        job=JobRuntimeSpec.model_validate(job.job_json),
        build=_build_claim(job.plugin_build, settings),
    )


@router.post("/jobs/reconcile", response_model=RunnerReconcileResponse)
def reconcile_jobs(
    request: RunnerClaimRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
) -> RunnerReconcileResponse:
    failed_jobs = RunnerOperationService(session).fail_interrupted_jobs(
        request.runtime_type
    )
    return RunnerReconcileResponse(
        runtime_type=request.runtime_type,
        failed_jobs=failed_jobs,
    )


@router.post(
    "/jobs/{job_key}/cancellation", response_model=RunnerCancellationResponse
)
def get_job_cancellation(
    job_key: str,
    request: RunnerClaimRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
) -> RunnerCancellationResponse:
    job = _matching_job(session, job_key, request.runtime_type)
    return RunnerCancellationResponse(
        job_id=job.job_key,
        cancel_requested=job.cancel_requested,
    )


@router.post("/jobs/{job_key}/events", response_model=RunnerEventAcceptedResponse)
def append_job_event(
    job_key: str,
    request: RunnerJobEventRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> RunnerEventAcceptedResponse:
    job = _matching_job(session, job_key, request.runtime_type)
    if job.status == JobStatus.PREPARING:
        job = HubRepository(session).transition_job(
            job, JobStatus.RUNNING, expected_status=JobStatus.PREPARING
        )
    elif job.status != JobStatus.RUNNING:
        raise _job_state_conflict()
    _append_event(storage, job, request, settings)
    return RunnerEventAcceptedResponse(id=job.job_key, status="RUNNING")


@router.post("/jobs/{job_key}/complete", response_model=RunnerCompletionResponse)
def complete_job(
    job_key: str,
    request: RunnerJobCompleteRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> RunnerCompletionResponse:
    """Record the runner-reported terminal result for one Job.

    A SUCCESS result first registers every self-reported output file, then the
    Hub enforces the Build manifest's output contract (G6/R5): a required
    ``file``/``files`` output with no registered file turns the reported
    SUCCESS into FAILED with the stable ``PLUGIN_OUTPUT_MISSING`` error code.
    ``object`` outputs are explicitly exempt — see
    :func:`_missing_required_file_outputs`.

    Runner-reported errors are stored through
    :func:`hub_server.services.failures.failure_summary`, which prefixes the
    stable error code (``CODE: message``) so the zero-migration G8 failure
    classification can derive ``failure_class`` from the stored summary.
    """
    job = _matching_job(session, job_key, request.runtime_type)
    if request.result.job_id != job_key:
        raise HubError(
            code="RUNNER_COMPLETION_INVALID",
            message="Job 结果标识不匹配",
            status_code=422,
        )
    if job.status == JobStatus.PREPARING:
        job = HubRepository(session).transition_job(
            job, JobStatus.RUNNING, expected_status=JobStatus.PREPARING
        )
    terminal_status = JobStatus(request.result.status)
    error_summary = failure_summary(request.result.error)
    if terminal_status is JobStatus.SUCCESS:
        try:
            for output in request.result.files:
                JobWorkspaceService(storage).register_output(
                    job,
                    logical_name=output.name,
                    relative_path=output.path,
                    mime_type="application/octet-stream",
                )
        except HubError as error:
            # Audit M-3 (E-06): a storage-side registration failure must not
            # bounce the runner with a 5xx — that used to cascade through the
            # runner restart reconcile into every other Job of the runtime.
            # The failure is absorbed into this one Job's terminal state and
            # answered with 200 so the runner main loop keeps running.
            terminal_status = JobStatus.FAILED
            error_summary = f"{PLUGIN_OUTPUT_REGISTER_FAILED}: {error.message}"
        else:
            # First terminal failure wins: when registration already failed the
            # required-output check would only add secondary noise on top of the
            # harder "reported file does not exist" signal.
            missing = _missing_required_file_outputs(job)
            if missing:
                # The runner reported SUCCESS, but the manifest output contract is
                # not satisfied: flip to FAILED through the same state machine as
                # any other failure instead of trusting the self-reported status.
                terminal_status = JobStatus.FAILED
                error_summary = f"{PLUGIN_OUTPUT_MISSING}: {', '.join(missing)}"
    try:
        completed = HubRepository(session).transition_job(
            job,
            terminal_status,
            error_summary=error_summary,
            exit_code=request.exit_code,
        )
    except ValueError as error:
        raise _job_state_conflict() from error
    return RunnerCompletionResponse.model_validate(
        {"id": completed.job_key, "status": completed.status}
    )


def _missing_required_file_outputs(job: Job) -> list[str]:
    """Return required ``file``/``files`` outputs for which no file was registered.

    G6 (R5): only file-producing outputs are enforced, by "the file has been
    registered" — a required output whose name never appeared among the
    runner-registered OUTPUT files fails the job. ``object`` outputs are
    explicitly exempt: they never produce a file (they are structured values
    inside ``PluginResult.data``), and manifest v1.0 defines no server-side
    contract for validating that structure. Enforcing them would silently fail
    every honest plugin; the semantics await the post-B8 contract version.
    """
    manifest = PluginManifest.model_validate(job.plugin_build.plugin_version.manifest_json)
    registered = {
        association.logical_name for association in job.files if association.role == "OUTPUT"
    }
    return [
        output.name
        for output in manifest.outputs
        if output.required and output.type in {"file", "files"} and output.name not in registered
    ]


def _build_claim(build: PluginBuild, settings: HubSettings) -> RunnerBuildClaim:
    if build.package_path is None:
        raise HubError(
            code="RUNNER_BUILD_INVALID",
            message="插件 Build 存储尚未准备完成",
            status_code=409,
        )
    manifest = PluginBuildManifest.model_validate(build.build_metadata_json)
    environment = build.environment
    memory_mb, cpus = _resolve_resource_limits(settings, build)
    return RunnerBuildClaim(
        build_id=build.build_key,
        runtime_type=manifest.runtime.type,
        runtime=manifest.runtime,
        runtime_archive=build.runtime_archive_path,
        manifest=f"{build.package_path}/plugin.yaml",
        source=f"{build.package_path}/plugin",
        environment_path=(environment.environment_path if environment else None),
        image_digest=(environment.image_digest if environment else None),
        memory_mb=memory_mb,
        cpus=cpus,
    )


def _resolve_resource_limits(
    settings: HubSettings, build: PluginBuild
) -> tuple[int | None, float | None]:
    """Resolve per-job resource caps for one Build (B7/G7/R6).

    Priority: the source manifest's explicit ``execution.memory_mb``/``cpus``
    declaration wins over the platform default; when the platform default node
    is disabled (``runner.resource_limits: null``) an undeclared plugin runs
    without any resource limit. The values are resolved fresh at claim time,
    so recalibrating the defaults never requires touching stored Builds.

    The platform default is also the CEILING: an explicit declaration can only
    narrow it. Trusting an author-declared ``memory_mb: 999999`` on a shared
    2-core host would void the whole protection (audit H-3), so explicit values
    are clamped with ``min``; a plugin that genuinely needs more gets it by
    raising the platform setting.
    """
    execution = PluginManifest.model_validate(build.plugin_version.manifest_json).execution
    default_limits = settings.runner.resource_limits
    memory_mb = execution.memory_mb
    if memory_mb is None and default_limits is not None:
        memory_mb = default_limits.memory_mb
    elif memory_mb is not None and default_limits is not None:
        memory_mb = min(memory_mb, default_limits.memory_mb)
    cpus = execution.cpus
    if cpus is None and default_limits is not None:
        cpus = default_limits.cpus
    elif cpus is not None and default_limits is not None:
        cpus = min(cpus, default_limits.cpus)
    return memory_mb, cpus


def _matching_job(session: Session, job_key: str, runtime_type: str) -> Job:
    job = session.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        raise HubError(code="JOB_NOT_FOUND", message="Job 不存在", status_code=404)
    if job.runtime_type != runtime_type:
        raise HubError(
            code="RUNNER_RUNTIME_MISMATCH",
            message="Runner 运行时与 Job 不匹配",
            status_code=409,
        )
    return job


def _append_event(
    storage: LocalStorage,
    job: Job,
    request: RunnerJobEventRequest,
    settings: HubSettings,
) -> None:
    """Append one runner event below ``<workspace>/meta/`` (audit M-2).

    ``logs/`` is mounted read-write into the plugin container, so the event
    stream used to sit within the plugin's write reach. ``meta/`` is never
    mounted (see the Docker volume map), keeping the Hub's record out of the
    plugin's reach.

    Audit M-4: the per-Job stream is capped at ``runner.event_log_max_count``
    events / ``runner.event_log_max_bytes`` bytes. Once a cap is reached one
    system event records the truncation and further events for that Job are
    dropped — the request still succeeds so the runner is never blocked on
    telemetry.
    """
    if job.workspace_path != f"jobs/{job.job_key}":
        raise HubError(
            code="RUNNER_JOB_INVALID",
            message="Job 工作区无效",
            status_code=409,
        )
    event_path = storage.open_relative(event_log_relative_path(job.workspace_path))
    event_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(request.event.model_dump(mode="json"), ensure_ascii=False) + "\n"
    incoming_bytes = len(line.encode("utf-8"))
    budget = _event_budget(event_path, job.job_key)
    if budget.exceeded(
        settings.runner.event_log_max_count,
        settings.runner.event_log_max_bytes,
        incoming_bytes,
    ):
        if not budget.truncated:
            budget.truncated = True
            marker = LogEvent(
                protocol_version="1.0",
                type="log",
                level=LogLevel.ERROR,
                message=(
                    "事件日志达到上限; 本 Job 后续事件不再记录 "
                    f"(count>={settings.runner.event_log_max_count} or "
                    f"bytes>={settings.runner.event_log_max_bytes})"
                ),
            ).model_dump(mode="json")
            budget.record(
                _write_event_line(
                    event_path,
                    json.dumps(marker, ensure_ascii=False) + "\n",
                )
            )
        return
    budget.record(_write_event_line(event_path, line))


def _write_event_line(event_path: Path, line: str) -> int:
    """Append one encoded line and return its byte size."""
    encoded_size = len(line.encode("utf-8"))
    with event_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(line)
        output.flush()
        os.fsync(output.fileno())
    return encoded_size


class _EventBudget:
    """In-process per-Job event counters, re-derived when the file disagrees.

    The cache is validated against the file's size on every append: a Hub
    restart (cold cache), a concurrent writer, or any other divergence simply
    re-scans the file once, so the cap can only ever under-count transiently,
    never unbound the stream.
    """

    __slots__ = ("count", "expected_size", "size_bytes", "truncated")

    def __init__(self, count: int, size_bytes: int) -> None:
        self.count = count
        self.size_bytes = size_bytes
        self.expected_size = size_bytes
        self.truncated = False

    def exceeded(self, max_count: int, max_bytes: int, incoming_bytes: int) -> bool:
        if self.count >= max_count:
            return True
        return self.size_bytes + incoming_bytes > max_bytes

    def record(self, written_bytes: int) -> None:
        self.count += 1
        self.size_bytes += written_bytes
        self.expected_size += written_bytes


_EVENT_BUDGETS: dict[str, _EventBudget] = {}


def _event_budget(event_path: Path, job_key: str) -> _EventBudget:
    # Keyed by the concrete path so distinct storage roots (tests, multi-app
    # processes) never share counters; validated by size so a stale entry from
    # a restarted Hub is re-derived instead of trusted.
    cache_key = f"{job_key}:{event_path}"
    current_size: int | None = None
    budget = _EVENT_BUDGETS.get(cache_key)
    if budget is not None:
        try:
            current_size = event_path.stat().st_size
        except OSError:
            current_size = None
        if current_size is not None and current_size == budget.expected_size:
            return budget
    count, size_bytes = _scan_event_file(event_path)
    budget = _EventBudget(count, size_bytes)
    _EVENT_BUDGETS[cache_key] = budget
    return budget


def _scan_event_file(event_path: Path) -> tuple[int, int]:
    """Count lines and bytes of an existing event log (cold-start budget)."""
    try:
        raw = event_path.read_bytes()
    except OSError:
        return 0, 0
    if not raw:
        return 0, 0
    return raw.count(b"\n"), len(raw)


def _job_state_conflict() -> HubError:
    return HubError(
        code="JOB_STATE_CONFLICT",
        message="Job 状态冲突",
        status_code=409,
    )
