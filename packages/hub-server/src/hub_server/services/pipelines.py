"""Linear pipeline run creation and step advancement."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from python_hub_contracts import JobStatus, RuntimeType
from sqlalchemy import select
from sqlalchemy.orm import Session

from hub_server.dependencies_auth import Actor
from hub_server.errors import HubError
from hub_server.models import Job, JobFile, Pipeline, PipelineRun
from hub_server.services.audit import record as audit
from hub_server.services.jobs import JobService
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

_PREV_PREFIX = "$prev."
_RUN_RUNNING = "RUNNING"
_RUN_SUCCEEDED = "SUCCEEDED"
_RUN_FAILED = "FAILED"
_NON_TERMINAL_JOB_STATUSES = frozenset({"PENDING", "PREPARING", "RUNNING", "CANCEL_REQUESTED"})
_FAILURE_JOB_STATUSES = frozenset(
    {JobStatus.FAILED.value, JobStatus.CANCELLED.value, JobStatus.TIMED_OUT.value}
)
_ALLOWED_RUNTIME_TYPES = frozenset({"conda-pack", "docker"})
_SYSTEM_ACTOR = Actor(kind="anonymous", id=None, name="system", role="admin")


def start_run(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    pipeline: Pipeline,
    actor: Actor,
    request_ip: str | None = None,
) -> tuple[PipelineRun, Job]:
    """Create one RUNNING run and the Job for its first step."""
    steps = _pipeline_steps(pipeline)
    run = PipelineRun(pipeline_id=pipeline.id, state=_RUN_RUNNING, current_step=0)
    session.add(run)
    session.flush()
    plugin_id, version, runtime_type, inputs, params = _step_fields(steps[0])
    if _prev_reference(inputs) is not None:
        raise _invalid("首步输入不能引用 $prev 输出")
    job = JobService(session, storage, settings).create(
        plugin_id=plugin_id,
        version=version,
        runtime_type=cast(RuntimeType, runtime_type),
        inputs=inputs,
        params=params,
        owner_user_id=actor.id if actor.kind == "user" else None,
    )
    job.pipeline_run_id = run.id
    _audit_step(session, actor, run, step=0, job=job, ip=request_ip)
    session.commit()
    return run, job


def advance_runs(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    now: datetime | None = None,
) -> int:
    """Advance every RUNNING run whose latest Job reached a terminal state.

    A successful step Job either spawns the next step's Job (with ``$prev.``
    inputs rewired to the produced file keys) or terminates the run; a failed,
    cancelled, or timed-out Job fails the run. Returns the number of runs that
    either advanced one step or terminated. ``now`` is reserved for future
    timeout enforcement.
    """
    processed = 0
    runs = session.scalars(
        select(PipelineRun).where(PipelineRun.state == _RUN_RUNNING).order_by(PipelineRun.id)
    ).all()
    for run in runs:
        latest_job = session.scalar(
            select(Job).where(Job.pipeline_run_id == run.id).order_by(Job.id.desc()).limit(1)
        )
        if latest_job is None:
            continue
        status = latest_job.status
        if status in _NON_TERMINAL_JOB_STATUSES:
            continue
        if status == JobStatus.SUCCESS.value:
            _advance_successful_run(session, storage, settings, run, latest_job)
        elif status in _FAILURE_JOB_STATUSES:
            _fail_run(session, run, reason=f"步骤 Job {latest_job.job_key} 以 {status} 结束")
        else:
            continue
        processed += 1
    return processed


def _advance_successful_run(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    run: PipelineRun,
    job: Job,
) -> None:
    steps = _pipeline_steps(run.pipeline)
    next_index = run.current_step + 1
    if next_index >= len(steps):
        run.state = _RUN_SUCCEEDED
        session.commit()
        return
    _run_next_step(session, storage, settings, run, steps[next_index], next_index, job)


def _run_next_step(
    session: Session,
    storage: LocalStorage,
    settings: HubSettings,
    run: PipelineRun,
    step: dict[str, object],
    index: int,
    previous_job: Job,
) -> None:
    plugin_id, version, runtime_type, inputs, params = _step_fields(step)
    resolved, unresolved = _resolve_inputs(session, previous_job, inputs)
    if unresolved is not None:
        _fail_run(session, run, reason=f"输入 {unresolved} 引用的上一步输出不存在")
        return
    job = JobService(session, storage, settings).create(
        plugin_id=plugin_id,
        version=version,
        runtime_type=cast(RuntimeType, runtime_type),
        inputs=resolved,
        params=params,
    )
    job.pipeline_run_id = run.id
    run.current_step = index
    _audit_step(session, _SYSTEM_ACTOR, run, step=index, job=job, ip=None)
    session.commit()


def _resolve_inputs(
    session: Session, previous_job: Job, inputs: dict[str, object]
) -> tuple[dict[str, object], str | None]:
    """Rewire ``$prev.<name>`` values to the produced file keys of the previous Job.

    Returns the resolved inputs plus the first unresolvable reference, if any.
    """
    outputs: dict[str, str] | None = None
    resolved: dict[str, object] = {}
    for key, value in inputs.items():
        if not (isinstance(value, str) and value.startswith(_PREV_PREFIX)):
            resolved[key] = value
            continue
        if outputs is None:
            outputs = _output_file_keys(session, previous_job)
        file_key = outputs.get(value[len(_PREV_PREFIX) :])
        if file_key is None:
            return {}, value
        resolved[key] = file_key
    return resolved, None


def _output_file_keys(session: Session, job: Job) -> dict[str, str]:
    """Map the Job's OUTPUT logical names to their immutable file keys."""
    associations = session.scalars(
        select(JobFile).where(JobFile.job_id == job.id, JobFile.role == "OUTPUT")
    ).all()
    return {
        association.logical_name: association.file_record.file_key for association in associations
    }


def _fail_run(session: Session, run: PipelineRun, *, reason: str) -> None:
    run.state = _RUN_FAILED
    audit(
        session,
        actor_type=_SYSTEM_ACTOR.kind,
        actor_id=None,
        actor_name=_SYSTEM_ACTOR.name,
        action="pipeline.run_failed",
        resource_type="pipeline_run",
        resource_id=str(run.id),
        detail={"run_id": run.id, "current_step": run.current_step, "reason": reason},
        result="denied",
    )
    session.commit()


def _audit_step(
    session: Session,
    actor: Actor,
    run: PipelineRun,
    *,
    step: int,
    job: Job,
    ip: str | None,
) -> None:
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="pipeline.step",
        resource_type="pipeline_run",
        resource_id=str(run.id),
        detail={"run_id": run.id, "step": step, "job_id": job.job_key},
        ip=ip,
    )


def _pipeline_steps(pipeline: Pipeline) -> list[dict[str, object]]:
    raw_steps = pipeline.steps_json
    if not isinstance(raw_steps, list) or not raw_steps:
        raise _invalid("管道尚未配置任何步骤")
    steps: list[dict[str, object]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            raise _invalid("管道步骤格式无效")
        steps.append(dict(raw_step))
    return steps


def _step_fields(
    step: dict[str, object],
) -> tuple[str, str, str, dict[str, object], dict[str, object]]:
    plugin_id = step.get("plugin_id")
    version = step.get("version")
    runtime_type = step.get("runtime_type")
    inputs = step.get("inputs", {})
    params = step.get("params", {})
    if not isinstance(plugin_id, str) or not plugin_id:
        raise _invalid("步骤缺少有效的 plugin_id")
    if not isinstance(version, str) or not version:
        raise _invalid("步骤缺少有效的 version")
    if not isinstance(runtime_type, str) or runtime_type not in _ALLOWED_RUNTIME_TYPES:
        raise _invalid("步骤 runtime_type 仅支持 conda-pack 或 docker")
    if not isinstance(inputs, dict) or not isinstance(params, dict):
        raise _invalid("步骤 inputs 与 params 必须是对象")
    return plugin_id, version, runtime_type, dict(inputs), dict(params)


def _prev_reference(inputs: dict[str, object]) -> str | None:
    for key, value in inputs.items():
        if isinstance(value, str) and value.startswith(_PREV_PREFIX):
            return key
    return None


def _invalid(message: str) -> HubError:
    return HubError(code="PIPELINE_INVALID", message=message, status_code=422)
