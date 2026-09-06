"""Private API used by isolated Conda and Docker runner services."""

from __future__ import annotations

import hmac
import json
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from python_hub_contracts import JobRuntimeSpec, JobStatus, PluginBuildManifest
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.errors import HubError
from hub_server.models import Job, PluginBuild
from hub_server.repositories import HubRepository
from hub_server.schemas import (
    RunnerBuildClaim,
    RunnerClaimRequest,
    RunnerCompletionResponse,
    RunnerEventAcceptedResponse,
    RunnerJobClaimResponse,
    RunnerJobCompleteRequest,
    RunnerJobEventRequest,
    RunnerJobPaths,
    RunnerOperationClaimResponse,
    RunnerOperationCompleteRequest,
)
from hub_server.services.runner_operations import RunnerOperationService
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
        build=_build_claim(operation.plugin_build),
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
        build=_build_claim(job.plugin_build),
    )


@router.post("/jobs/{job_key}/events", response_model=RunnerEventAcceptedResponse)
def append_job_event(
    job_key: str,
    request: RunnerJobEventRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
) -> RunnerEventAcceptedResponse:
    job = _matching_job(session, job_key, request.runtime_type)
    if job.status == JobStatus.PREPARING:
        job = HubRepository(session).transition_job(
            job, JobStatus.RUNNING, expected_status=JobStatus.PREPARING
        )
    elif job.status != JobStatus.RUNNING:
        raise _job_state_conflict()
    _append_event(storage, job, request)
    return RunnerEventAcceptedResponse(id=job.job_key, status="RUNNING")


@router.post("/jobs/{job_key}/complete", response_model=RunnerCompletionResponse)
def complete_job(
    job_key: str,
    request: RunnerJobCompleteRequest,
    _: RunnerAuthorization,
    session: Annotated[Session, Depends(get_session)],
) -> RunnerCompletionResponse:
    job = _matching_job(session, job_key, request.runtime_type)
    if request.result.job_id != job_key:
        raise HubError(
            code="RUNNER_COMPLETION_INVALID",
            message="Job 结果标识不匹配",
            status_code=422,
        )
    try:
        completed = HubRepository(session).transition_job(
            job,
            JobStatus(request.result.status),
            error_summary=(request.result.error.message if request.result.error else None),
            exit_code=request.exit_code,
        )
    except ValueError as error:
        raise _job_state_conflict() from error
    return RunnerCompletionResponse.model_validate(
        {"id": completed.job_key, "status": completed.status}
    )


def _build_claim(build: PluginBuild) -> RunnerBuildClaim:
    if build.package_path is None:
        raise HubError(
            code="RUNNER_BUILD_INVALID",
            message="插件 Build 存储尚未准备完成",
            status_code=409,
        )
    manifest = PluginBuildManifest.model_validate(build.build_metadata_json)
    environment = build.environment
    return RunnerBuildClaim(
        build_id=build.build_key,
        runtime_type=manifest.runtime.type,
        runtime=manifest.runtime,
        runtime_archive=build.runtime_archive_path,
        manifest=f"{build.package_path}/plugin.yaml",
        source=f"{build.package_path}/plugin",
        environment_path=(environment.environment_path if environment else None),
        image_digest=(environment.image_digest if environment else None),
    )


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
    storage: LocalStorage, job: Job, request: RunnerJobEventRequest
) -> None:
    if job.workspace_path != f"jobs/{job.job_key}":
        raise HubError(
            code="RUNNER_JOB_INVALID",
            message="Job 工作区无效",
            status_code=409,
        )
    log_directory = storage.open_relative(f"{job.workspace_path}/logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    event_path = log_directory / "events.jsonl"
    line = json.dumps(request.event.model_dump(mode="json"), ensure_ascii=False) + "\n"
    with event_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(line)
        output.flush()
        os.fsync(output.fileno())


def _job_state_conflict() -> HubError:
    return HubError(
        code="JOB_STATE_CONFLICT",
        message="Job 状态冲突",
        status_code=409,
    )
