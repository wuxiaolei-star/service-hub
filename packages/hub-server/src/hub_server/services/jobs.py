"""Public Job creation, validation, cancellation, logs, and outputs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import cast

from python_hub_contracts import JobStatus, PluginManifest, RuntimeType
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import FileRecord, Job, Plugin, PluginBuild, PluginVersion
from hub_server.services.workspaces import JobWorkspaceService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

_TERMINAL_STATUSES = {
    JobStatus.SUCCESS.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
    JobStatus.TIMED_OUT.value,
}


class JobService:
    """Create and expose Jobs through the public API."""

    def __init__(self, session: Session, storage: LocalStorage, settings: HubSettings) -> None:
        self._session = session
        self._storage = storage
        self._settings = settings

    def create(
        self,
        *,
        plugin_id: str,
        version: str,
        runtime_type: RuntimeType | None,
        inputs: Mapping[str, object],
        params: Mapping[str, object],
        owner_user_id: int | None = None,
        replayed_from: str | None = None,
    ) -> Job:
        selected_runtime: RuntimeType = runtime_type or "docker"
        build = self._resolve_enabled_build(plugin_id, version, selected_runtime)
        manifest = PluginManifest.model_validate(build.plugin_version.manifest_json)
        validated_params = self._validate_params(manifest, params)
        validated_inputs, input_records = self._validate_inputs(manifest, inputs)
        job = Job(
            plugin_build=build,
            runtime_type=selected_runtime,
            runtime_fingerprint=build.runtime_fingerprint,
            status=JobStatus.PENDING.value,
            params_json=validated_params,
            inputs_json=validated_inputs,
            timeout_seconds=manifest.execution.timeout,
            cancel_requested=False,
            owner_user_id=owner_user_id,
            replayed_from=replayed_from,
        )
        try:
            self._session.add(job)
            self._session.flush()
            JobWorkspaceService(self._storage).prepare(job, input_records)
            self._session.commit()
        except HubError:
            self._session.rollback()
            raise
        except Exception as error:
            self._session.rollback()
            raise HubError(
                code="JOB_CREATE_FAILED",
                message="创建 Job 失败",
                status_code=500,
            ) from error
        self._session.refresh(job)
        return job

    def cancel(self, job_key: str) -> Job:
        job = self._get_job(job_key)
        if job.status not in _TERMINAL_STATUSES:
            job.cancel_requested = True
            self._session.commit()
            self._session.refresh(job)
        return job

    def terminal_job(self, job_key: str) -> Job:
        """Return a terminal Job, or raise the stable rerun precondition errors."""
        job = self._get_job(job_key)
        if job.status not in _TERMINAL_STATUSES:
            raise HubError(
                code="JOB_NOT_TERMINAL",
                message="Job 尚未结束，不能重跑",  # noqa: RUF001
                status_code=409,
            )
        return job

    def list_jobs(
        self,
        *,
        status: Sequence[str] | None = None,
        plugin_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Job], int]:
        """Return filtered Jobs newest-first plus the total matching the filters."""
        conditions: list[ColumnElement[bool]] = []
        if status:
            conditions.append(Job.status.in_(list(status)))
        stmt = select(Job)
        if plugin_id is not None:
            stmt = (
                stmt.join(Job.plugin_build)
                .join(PluginBuild.plugin_version)
                .join(PluginVersion.plugin)
            )
            conditions.append(Plugin.plugin_key == plugin_id)
        if conditions:
            stmt = stmt.where(*conditions)
        total = self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        jobs = list(
            self._session.scalars(
                stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit).offset(offset)
            )
        )
        return jobs, int(total or 0)

    def logs(
        self, job_key: str, *, cursor: int, limit: int
    ) -> tuple[list[dict[str, object]], int | None]:
        job = self._get_job(job_key)
        if job.workspace_path is None:
            return [], None
        log_path = self._storage.open_relative(f"{job.workspace_path}/logs/events.jsonl")
        if not log_path.exists():
            return [], None
        lines = log_path.read_text("utf-8").splitlines()
        selected = lines[cursor : cursor + limit]
        events: list[dict[str, object]] = []
        for line in selected:
            loaded = json.loads(line)
            if isinstance(loaded, dict):
                events.append(cast(dict[str, object], loaded))
        next_cursor = cursor + len(selected) if cursor + len(selected) < len(lines) else None
        return events, next_cursor

    def outputs(self, job_key: str) -> list[FileRecord]:
        job = self._get_job(job_key)
        if job.status != JobStatus.SUCCESS.value:
            raise HubError(
                code="JOB_NOT_SUCCESSFUL",
                message="Job 尚未成功完成",
                status_code=409,
            )
        records = [
            association.file_record
            for association in job.files
            if association.role == "OUTPUT"
        ]
        return records

    def _resolve_enabled_build(
        self, plugin_id: str, version: str, runtime_type: RuntimeType
    ) -> PluginBuild:
        build = self._session.scalar(
            select(PluginBuild)
            .join(PluginVersion)
            .join(Plugin)
            .where(
                Plugin.plugin_key == plugin_id,
                PluginVersion.version == version,
                PluginBuild.runtime_type == runtime_type,
                PluginBuild.target_os == self._settings.platform_os,
                PluginBuild.target_arch == self._settings.platform_arch,
                PluginBuild.status == "ENABLED",
            )
        )
        if build is None:
            raise HubError(
                code="PLUGIN_BUILD_NOT_ENABLED",
                message="指定插件 Build 不存在或未启用",
                status_code=404,
            )
        return build

    def _validate_params(
        self, manifest: PluginManifest, raw_params: Mapping[str, object]
    ) -> dict[str, object]:
        params = dict(raw_params)
        declared = {parameter.name: parameter for parameter in manifest.parameters}
        extra = set(params) - set(declared)
        if extra:
            raise _validation_error("插件参数包含未声明字段")
        resolved: dict[str, object] = {}
        for name, spec in declared.items():
            if name not in params:
                if spec.default is not None:
                    resolved[name] = spec.default
                    continue
                if spec.required:
                    raise _validation_error("缺少必填插件参数")
                continue
            value = params[name]
            if value is None and not spec.required:
                resolved[name] = None
                continue
            if not _matches_type(value, spec.type):
                raise _validation_error("插件参数类型无效")
            if spec.type == "enum" and value not in (spec.options or []):
                raise _validation_error("插件参数枚举值无效")
            if spec.type == "string_list":
                values = cast(list[str], value)
                if len(values) != len(set(values)):
                    raise _validation_error("插件字符串列表参数不能包含重复值")
                if spec.options is not None and any(
                    item not in spec.options for item in values
                ):
                    raise _validation_error("插件字符串列表参数包含未声明值")
                if spec.min is not None and len(values) < spec.min:
                    raise _validation_error("插件字符串列表参数数量不足")
                if spec.max is not None and len(values) > spec.max:
                    raise _validation_error("插件字符串列表参数数量过多")
            if isinstance(value, int | float):
                if spec.min is not None and value < spec.min:
                    raise _validation_error("插件参数小于最小值")
                if spec.max is not None and value > spec.max:
                    raise _validation_error("插件参数大于最大值")
            resolved[name] = value
        return resolved

    def _validate_inputs(
        self, manifest: PluginManifest, raw_inputs: Mapping[str, object]
    ) -> tuple[dict[str, object], list[FileRecord]]:
        inputs = dict(raw_inputs)
        declared = {item.name: item for item in manifest.inputs}
        extra = set(inputs) - set(declared)
        if extra:
            raise _validation_error("插件输入包含未声明字段")
        records: list[FileRecord] = []
        resolved: dict[str, object] = {}
        for name, spec in declared.items():
            if name not in inputs:
                if spec.required:
                    raise _validation_error("缺少必填插件输入")
                continue
            file_keys = _input_keys(inputs[name])
            if spec.type == "file" and len(file_keys) != 1:
                raise _validation_error("插件输入需要单个文件")
            if spec.min_count is not None and len(file_keys) < spec.min_count:
                raise _validation_error("插件输入文件数量不足")
            if spec.max_count is not None and len(file_keys) > spec.max_count:
                raise _validation_error("插件输入文件数量过多")
            input_records = self._files(file_keys)
            for record in input_records:
                if spec.extensions and (record.extension or "").lower() not in {
                    extension.lower() for extension in spec.extensions
                }:
                    raise _validation_error("插件输入文件扩展名无效")
                if spec.max_size is not None and record.size_bytes > spec.max_size:
                    raise _validation_error("插件输入文件超过大小限制")
            records.extend(input_records)
            resolved[name] = file_keys[0] if spec.type == "file" else file_keys
        return resolved, records

    def _files(self, file_keys: Iterable[str]) -> list[FileRecord]:
        records = list(
            self._session.scalars(
                select(FileRecord).where(
                    FileRecord.file_key.in_(list(file_keys)),
                    FileRecord.status == "AVAILABLE",
                )
            )
        )
        if len(records) != len(set(file_keys)):
            raise HubError(code="FILE_NOT_FOUND", message="输入文件不存在", status_code=404)
        return records

    def _get_job(self, job_key: str) -> Job:
        job = self._session.scalar(select(Job).where(Job.job_key == job_key))
        if job is None:
            raise HubError(code="JOB_NOT_FOUND", message="Job 不存在", status_code=404)
        return job


def _input_keys(value: object) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return value
    raise _validation_error("插件输入文件 ID 无效")


def _matches_type(value: object, parameter_type: str) -> bool:
    if parameter_type == "string":
        return isinstance(value, str)
    if parameter_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if parameter_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if parameter_type == "boolean":
        return isinstance(value, bool)
    if parameter_type in {"enum", "datetime"}:
        return isinstance(value, str)
    if parameter_type == "string_list":
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item) for item in value)
        )
    return False


def _validation_error(message: str) -> HubError:
    return HubError(code="JOB_REQUEST_INVALID", message=message, status_code=422)
