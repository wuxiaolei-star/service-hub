"""Authentication dependencies enforcing the public API role matrix."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings
from hub_server.errors import HubError
from hub_server.services.auth import AuthService
from hub_server.settings import HubSettings

SESSION_COOKIE_NAME = "hub_session"
API_KEY_PREFIX = "hub_"

ROLE_ORDER: dict[str, int] = {
    "viewer": 0,
    "operator": 1,
    "publisher": 2,
    "admin": 3,
}


@dataclass(frozen=True)
class Actor:
    """The authenticated identity behind one request."""

    kind: str  # "user" | "api_key" | "anonymous"
    id: int | None
    name: str
    role: str


def _auth_required(message: str = "请先登录") -> HubError:
    return HubError(code="AUTH_REQUIRED", message=message, status_code=401)


def _extract_credential(request: Request) -> str | None:
    # An explicit Authorization header wins over the browser session cookie so
    # machine clients never get shadowed by an interactive login.
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return request.cookies.get(SESSION_COOKIE_NAME)


def resolve_actor(
    request: Request,
    session: Session,
    settings: HubSettings,
) -> Actor:
    """Resolve the request credential to an actor, or raise AUTH_REQUIRED."""
    if settings.auth.mode == "off":
        return Actor(kind="anonymous", id=None, name="anonymous", role="admin")

    credential = _extract_credential(request)
    if credential is None:
        raise _auth_required()

    service = AuthService(session)
    if credential.startswith(API_KEY_PREFIX):
        key = service.resolve_api_key(credential)
        if key is None:
            raise _auth_required("凭证无效或已吊销")
        return Actor(kind="api_key", id=key.id, name=key.name, role=key.role)

    record = service.resolve_session(credential)
    if record is None:
        raise _auth_required("凭证无效或已过期")
    return Actor(
        kind="user",
        id=record.user.id,
        name=record.user.username,
        role=record.user.role,
    )


def require_role(minimum: str) -> Callable[..., Actor]:
    """Build a dependency that requires at least one role from the matrix."""
    minimum_rank = ROLE_ORDER[minimum]

    def dependency(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
        settings: Annotated[HubSettings, Depends(get_settings)],
    ) -> Actor:
        actor = resolve_actor(request, session, settings)
        if ROLE_ORDER[actor.role] < minimum_rank:
            raise HubError(code="FORBIDDEN", message="权限不足", status_code=403)
        return actor

    return dependency
