"""Atomic installation-operation completion shared by both runner types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from python_hub_contracts import JobStatus, RuntimeType
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import Job, RunnerOperation


class RunnerOperationService:
    """Complete a claimed operation and its Build/Environment in one transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def fail_interrupted_jobs(self, runtime_type: RuntimeType) -> int:
        """Mark jobs abandoned by a restarted runtime runner as failed."""
        now = datetime.now(UTC)
        operations = self._session.scalars(
            select(RunnerOperation).where(
                RunnerOperation.runtime_type == runtime_type,
                RunnerOperation.kind == "INSTALL",
                RunnerOperation.status == "PREPARING",
            )
        )
        for operation in operations:
            operation.status = "FAILED"
            operation.error_summary = "HUB_RESTARTED"
            operation.completed_at = now
            build = operation.plugin_build
            build.status = "FAILED"
            build.error_summary = "HUB_RESTARTED"
            if build.environment is not None:
                build.environment.status = "FAILED"
                build.environment.error_summary = "HUB_RESTARTED"
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Job)
                .where(
                    Job.runtime_type == runtime_type,
                    Job.status.in_([JobStatus.PREPARING.value, JobStatus.RUNNING.value]),
                )
                .values(
                    status=JobStatus.FAILED.value,
                    error_summary="HUB_RESTARTED",
                    finished_at=now,
                    updated_at=now,
                )
            ),
        )
        self._session.commit()
        return int(result.rowcount or 0)

    def complete(
        self,
        operation_key: str,
        *,
        runtime_type: RuntimeType,
        status: str,
        environment_path: str | None = None,
        image_digest: str | None = None,
        metadata: dict[str, object] | None = None,
        error_summary: str | None = None,
        exit_code: int | None = None,
    ) -> RunnerOperation:
        operation = self._session.scalar(
            select(RunnerOperation).where(RunnerOperation.operation_key == operation_key)
        )
        if operation is None:
            raise HubError(
                code="RUNNER_OPERATION_NOT_FOUND",
                message="Runner 安装操作不存在",
                status_code=404,
            )
        if operation.runtime_type != runtime_type:
            raise HubError(
                code="RUNNER_RUNTIME_MISMATCH",
                message="Runner 运行时与安装操作不匹配",
                status_code=409,
            )
        if operation.status != "PREPARING":
            raise HubError(
                code="RUNNER_OPERATION_STATE_CONFLICT",
                message="Runner 安装操作状态冲突",
                status_code=409,
            )
        if status not in {"SUCCESS", "FAILED"}:
            raise ValueError("operation completion status must be SUCCESS or FAILED")

        build = operation.plugin_build
        environment = build.environment
        if environment is None:
            raise RuntimeError("plugin Build environment is missing")
        if status == "SUCCESS":
            self._validate_success_metadata(
                operation,
                environment_path=environment_path,
                image_digest=image_digest,
            )
        now = datetime.now(UTC)
        terminal_status = "READY" if status == "SUCCESS" else "FAILED"
        operation.status = status
        operation.exit_code = exit_code
        operation.error_summary = error_summary
        operation.completed_at = now
        build.status = terminal_status
        build.error_summary = error_summary
        environment.status = terminal_status
        environment.error_summary = error_summary
        if environment_path is not None:
            environment.environment_path = environment_path
        if image_digest is not None:
            environment.image_digest = image_digest
        if metadata:
            environment.metadata_json = {**environment.metadata_json, **metadata}
        if status == "SUCCESS":
            environment.healthy_at = now
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(operation)
        return operation

    @staticmethod
    def _validate_success_metadata(
        operation: RunnerOperation,
        *,
        environment_path: str | None,
        image_digest: str | None,
    ) -> None:
        build = operation.plugin_build
        if build.runtime_type == "conda-pack":
            if environment_path != f"environments/{build.build_key}":
                raise HubError(
                    code="RUNNER_COMPLETION_INVALID",
                    message="Conda 环境路径无效",
                    status_code=422,
                )
        elif image_digest != build.runtime_fingerprint:
            raise HubError(
                code="RUNNER_COMPLETION_INVALID",
                message="Docker 镜像摘要与 Build 不匹配",
                status_code=422,
            )
