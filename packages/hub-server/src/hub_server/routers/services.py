"""Authenticated public API for long-running Docker service definitions."""

from __future__ import annotations

import re
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings
from hub_server.dependencies_auth import Actor, actor_ip, get_actor, require_role
from hub_server.errors import HubError
from hub_server.models import ServiceDef
from hub_server.schemas import (
    ServiceCreateRequest,
    ServiceListResponse,
    ServiceMount,
    ServiceResponse,
    ServiceRuntimeResponse,
)
from hub_server.services.audit import record as audit
from hub_server.services.service_manager import get_service_manager
from hub_server.settings import HubSettings

router = APIRouter(
    prefix="/services", tags=["services"], dependencies=[Depends(require_role("viewer"))]
)

_SERVICE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _require_mounts_below_host_data_root(
    mounts: list[ServiceMount], settings: HubSettings
) -> None:
    """Allow bind mounts only below the Hub-managed host data directory.

    This is the architectural boundary of V3.0: a managed service container may
    read and write Hub data, but never arbitrary host paths such as `/etc` or
    the Docker socket. The service-manager re-validates the same rule so the
    two layers must agree before any container is created.
    """
    if not mounts:
        return
    root = (settings.docker.host_data_root or "").rstrip("/")
    if not root:
        raise HubError(
            code="SERVICE_MOUNT_FORBIDDEN",
            message="未配置宿主机数据目录时拒绝服务挂载",
            status_code=422,
        )
    prefix = root + "/"
    for mount in mounts:
        if not mount.source.startswith(prefix) or mount.source == root:
            raise HubError(
                code="SERVICE_MOUNT_FORBIDDEN",
                message=f"挂载来源必须位于宿主机数据目录 {root} 之内",
                status_code=422,
            )


class _Manager(Protocol):
    def deploy(self, payload: dict[str, object]) -> dict[str, object]: ...

    def status(self, name: str) -> dict[str, object]: ...

    def logs(self, name: str, tail: int) -> dict[str, object]: ...

    def action(self, name: str, action: str) -> dict[str, object]: ...

    def remove(self, name: str) -> dict[str, object]: ...


@router.get("", response_model=ServiceListResponse)
def list_services(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ServiceListResponse:
    """List saved definitions with their current manager-reported state.

    A saved definition can legitimately have no container (its image was refused,
    or it was never started). Whatever the manager says about one entry, it must
    not blank the catalogue: the failing entry is rendered as unknown and the
    rest of the list still answers.
    """
    manager: _Manager = get_service_manager(settings)
    definitions = session.query(ServiceDef).order_by(ServiceDef.name).all()
    items = [_response(definition, _safe_status(manager, definition)) for definition in definitions]
    return ServiceListResponse(items=items)


@router.post(
    "",
    response_model=ServiceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role("admin"))],
)
def create_service(
    request: ServiceCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ServiceResponse:
    """Persist and deploy one admin-authorized service definition."""
    _require_valid_name(request.name)
    _require_mounts_below_host_data_root(request.mounts, settings)
    if session.query(ServiceDef).filter_by(name=request.name).one_or_none() is not None:
        raise HubError(code="SERVICE_NAME_TAKEN", message="服务名称已存在", status_code=409)
    definition = _new_definition(request)
    session.add(definition)
    session.flush()
    manager: _Manager = get_service_manager(settings)
    try:
        manager_result = manager.deploy(_manager_payload(definition))
    except HubError:
        definition.desired_state = "STOPPED"
        session.commit()
        raise
    definition.desired_state = "RUNNING"
    _audit_change(session, actor, http_request, "service.create", definition)
    session.commit()
    return _response(definition, manager_result)


@router.put(
    "/{name}", response_model=ServiceResponse, dependencies=[Depends(require_role("admin"))]
)
def update_service(
    name: str,
    request: ServiceCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ServiceResponse:
    """Replace configuration and the same-name container idempotently."""
    _require_valid_name(name)
    if request.name != name:
        raise HubError(code="SERVICE_NAME_IMMUTABLE", message="服务名称不能修改", status_code=422)
    _require_mounts_below_host_data_root(request.mounts, settings)
    definition = _get_definition(session, name)
    _apply_definition(definition, request)
    session.flush()
    manager: _Manager = get_service_manager(settings)
    try:
        manager_result = manager.deploy(_manager_payload(definition))
    except HubError:
        definition.desired_state = "STOPPED"
        session.commit()
        raise
    definition.desired_state = "RUNNING"
    _audit_change(session, actor, http_request, "service.update", definition)
    session.commit()
    return _response(definition, manager_result)


@router.post(
    "/{name}/{action}",
    response_model=ServiceResponse,
    dependencies=[Depends(require_role("admin"))],
)
def lifecycle_action(
    name: str,
    action: Annotated[str, "start, stop, or restart"],
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> ServiceResponse:
    """Execute one admin-authorized lifecycle action through the manager."""
    if action not in {"start", "stop", "restart"}:
        raise HubError(code="SERVICE_ACTION_INVALID", message="不支持的服务操作", status_code=404)
    definition = _get_definition(session, name)
    manager: _Manager = get_service_manager(settings)
    result = manager.action(name, action)
    definition.desired_state = "STOPPED" if action == "stop" else "RUNNING"
    _audit_change(session, actor, http_request, f"service.{action}", definition)
    session.commit()
    return _response(definition, result)


@router.get("/{name}/logs", response_class=PlainTextResponse)
def get_service_logs(
    name: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
    tail: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> PlainTextResponse:
    """Return a bounded service log tail without exposing Docker directly."""
    _get_definition(session, name)
    result = get_service_manager(settings).logs(name, tail)
    logs = result.get("logs")
    if not isinstance(logs, str):
        raise HubError(
            code="SERVICE_MANAGER_UNAVAILABLE", message="服务管理器不可用", status_code=502
        )
    return PlainTextResponse(logs)


@router.delete("/{name}", dependencies=[Depends(require_role("admin"))])
def delete_service(
    name: str,
    actor: Annotated[Actor, Depends(get_actor)],
    http_request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> dict[str, object]:
    """Remove a service container first, then its persisted definition."""
    definition = _get_definition(session, name)
    get_service_manager(settings).remove(name)
    resource_id = str(definition.id)
    session.delete(definition)
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="service.delete",
        resource_type="service",
        resource_id=resource_id,
        detail={"name": name},
        ip=actor_ip(http_request),
    )
    session.commit()
    return {"name": name, "deleted": True}


def _new_definition(request: ServiceCreateRequest) -> ServiceDef:
    return ServiceDef(
        name=request.name,
        image=request.image,
        container_name=f"hub-svc-{request.name}",
        ports_json=[port.model_dump() for port in request.ports],
        env_json=dict(request.env),
        mounts_json=[mount.model_dump() for mount in request.mounts],
        command_json=list(request.command) if request.command is not None else None,
        user_label=request.user_label,
        desired_state="PENDING",
    )


def _apply_definition(definition: ServiceDef, request: ServiceCreateRequest) -> None:
    definition.image = request.image
    definition.ports_json = [port.model_dump() for port in request.ports]
    definition.env_json = dict(request.env)
    definition.mounts_json = [mount.model_dump() for mount in request.mounts]
    definition.command_json = list(request.command) if request.command is not None else None
    definition.user_label = request.user_label
    definition.desired_state = "PENDING"


def _get_definition(session: Session, name: str) -> ServiceDef:
    definition = session.query(ServiceDef).filter_by(name=name).one_or_none()
    if definition is None:
        raise HubError(code="SERVICE_NOT_FOUND", message="服务不存在", status_code=404)
    return definition


def _require_valid_name(name: str) -> None:
    if not _SERVICE_NAME.fullmatch(name):
        raise HubError(code="SERVICE_NAME_INVALID", message="服务名称格式无效", status_code=422)


def _manager_payload(definition: ServiceDef) -> dict[str, object]:
    return {
        "name": definition.name,
        "image": definition.image,
        "ports": list(definition.ports_json or []),
        "env": dict(definition.env_json or {}),
        "mounts": list(definition.mounts_json or []),
        "command": list(definition.command_json) if definition.command_json is not None else None,
        "user_label": definition.user_label,
    }


def _audit_change(
    session: Session,
    actor: Actor,
    http_request: Request,
    action: str,
    definition: ServiceDef,
) -> None:
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action=action,
        resource_type="service",
        resource_id=str(definition.id),
        detail={
            "name": definition.name,
            "image": definition.image,
            "desired_state": definition.desired_state,
        },
        ip=actor_ip(http_request),
    )


def _safe_status(manager: _Manager, definition: ServiceDef) -> dict[str, object]:
    """Ask the manager about one definition without letting a reply break the list.

    A 404 means the container is gone, and any other failure means the manager
    could not answer; both are rendered as an unknown runtime rather than raised.
    """
    try:
        return manager.status(definition.name)
    except HubError:
        return {}


def _response(definition: ServiceDef, manager_result: dict[str, object]) -> ServiceResponse:
    state = manager_result.get("state")
    manager_state = state if isinstance(state, str) else "unknown"
    return ServiceResponse(
        name=definition.name,
        image=definition.image,
        container_name=definition.container_name,
        desired_state=definition.desired_state,
        runtime=ServiceRuntimeResponse(
            state=manager_state,
            health=_health(manager_result),
            ports=_ports(manager_result),
        ),
    )


def _health(manager_result: dict[str, object]) -> str | None:
    health = manager_result.get("health")
    return health if isinstance(health, str) else None


def _ports(manager_result: dict[str, object]) -> dict[str, object] | None:
    ports = manager_result.get("ports")
    return ports if isinstance(ports, dict) else None
