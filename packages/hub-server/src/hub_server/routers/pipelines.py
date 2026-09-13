"""Linear pipeline CRUD and execution endpoints (viewer reads, operator writes)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings, get_storage
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import Pipeline, PipelineRun
from hub_server.services.audit import record as audit
from hub_server.services.pipelines import start_run
from hub_server.settings import HubSettings
from hub_server.storage import LocalStorage

router = APIRouter(
    prefix="/pipelines", tags=["pipelines"], dependencies=[Depends(require_role("viewer"))]
)

_ALLOWED_RUNTIME_TYPES = frozenset({"conda-pack", "docker"})
_PREV_PREFIX = "$prev."


class PipelineCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    steps: list[dict[str, object]]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def create_pipeline(
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    request: PipelineCreateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Register one linear pipeline of at least one validated step."""
    steps = _validate_steps(request.steps)
    existing = session.query(Pipeline).filter(Pipeline.name == request.name).one_or_none()
    if existing is not None:
        raise HubError(
            code="PIPELINE_NAME_TAKEN",
            message="管道名称已存在",
            status_code=status.HTTP_409_CONFLICT,
        )
    pipeline = Pipeline(name=request.name, steps_json=steps)
    session.add(pipeline)
    session.flush()
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="pipeline.create",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        detail={"name": pipeline.name, "steps": len(steps)},
        ip=actor_ip(http_request),
    )
    session.commit()
    return _pipeline_response(pipeline)


@router.get("")
def list_pipelines(session: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    """Return every registered pipeline ordered by id."""
    pipelines = session.query(Pipeline).order_by(Pipeline.id).all()
    return {"items": [_pipeline_response(pipeline) for pipeline in pipelines]}


@router.get("/runs")
def list_pipeline_runs(
    session: Annotated[Session, Depends(get_session)],
    pipeline_id: Annotated[int | None, Query()] = None,
) -> dict[str, object]:
    """Return the most recent 100 runs, newest first, optionally per pipeline."""
    query = session.query(PipelineRun).order_by(PipelineRun.id.desc())
    if pipeline_id is not None:
        query = query.filter(PipelineRun.pipeline_id == pipeline_id)
    runs = query.limit(100).all()
    return {"items": [_run_response(run) for run in runs]}


@router.get("/{pipeline_id}")
def get_pipeline(
    pipeline_id: int,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Return one pipeline row."""
    return _pipeline_response(_get_pipeline(session, pipeline_id))


@router.post(
    "/{pipeline_id}/execute",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("operator"))],
)
def execute_pipeline(
    pipeline_id: int,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
    storage: Annotated[LocalStorage, Depends(get_storage)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> dict[str, object]:
    """Start one run and create the Job for its first step."""
    pipeline = _get_pipeline(session, pipeline_id)
    run, job = start_run(
        session, storage, settings, pipeline, actor, request_ip=actor_ip(http_request)
    )
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="pipeline.execute",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        detail={"run_id": run.id, "job_id": job.job_key},
        ip=actor_ip(http_request),
    )
    session.commit()
    return {"run_id": run.id, "job_id": job.job_key, "state": run.state}


def _get_pipeline(session: Session, pipeline_id: int) -> Pipeline:
    pipeline = session.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HubError(code="PIPELINE_NOT_FOUND", message="管道不存在", status_code=404)
    return pipeline


def _validate_steps(raw_steps: list[dict[str, object]]) -> list[dict[str, object]]:
    if len(raw_steps) < 1:
        raise _invalid("管道至少需要一个步骤")
    normalized: list[dict[str, object]] = []
    for index, raw_step in enumerate(raw_steps):
        step = dict(raw_step)
        plugin_id = step.get("plugin_id")
        version = step.get("version")
        runtime_type = step.get("runtime_type")
        inputs = step.get("inputs", {})
        params = step.get("params", {})
        if not isinstance(plugin_id, str) or not plugin_id:
            raise _invalid(f"步骤 {index} 缺少有效的 plugin_id")
        if not isinstance(version, str) or not version:
            raise _invalid(f"步骤 {index} 缺少有效的 version")
        if not isinstance(runtime_type, str) or runtime_type not in _ALLOWED_RUNTIME_TYPES:
            raise _invalid(f"步骤 {index} runtime_type 仅支持 conda-pack 或 docker")
        if not isinstance(inputs, dict) or not isinstance(params, dict):
            raise _invalid(f"步骤 {index} inputs 与 params 必须是对象")
        if index == 0:
            for value in inputs.values():
                if isinstance(value, str) and value.startswith(_PREV_PREFIX):
                    raise _invalid("首步输入不能引用 $prev 输出")
        normalized.append(
            {
                "plugin_id": plugin_id,
                "version": version,
                "runtime_type": runtime_type,
                "inputs": dict(inputs),
                "params": dict(params),
            }
        )
    return normalized


def _invalid(message: str) -> HubError:
    return HubError(code="PIPELINE_INVALID", message=message, status_code=422)


def _pipeline_response(pipeline: Pipeline) -> dict[str, object]:
    return {
        "id": pipeline.id,
        "name": pipeline.name,
        "steps": list(pipeline.steps_json or []),
        "created_at": _utc_iso(pipeline.created_at),
        "updated_at": _utc_iso(pipeline.updated_at),
    }


def _run_response(run: PipelineRun) -> dict[str, object]:
    return {
        "id": run.id,
        "pipeline_id": run.pipeline_id,
        "state": run.state,
        "current_step": run.current_step,
        "created_at": _utc_iso(run.created_at),
    }


def _utc_iso(value: datetime) -> str:
    """Normalize SQLite's timezone-naive values to an explicit UTC timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
