"""Authentication endpoints for the public API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session, get_settings
from hub_server.dependencies_auth import (
    SESSION_COOKIE_NAME,
    Actor,
    actor_ip,
    require_role,
)
from hub_server.errors import HubError
from hub_server.models import UserRecord
from hub_server.services.audit import record as audit
from hub_server.services.auth import (
    AuthService,
    hash_password,
    sha256_hex,
    verify_password,
)
from hub_server.services.login_throttle import LoginThrottle, lockout_error
from hub_server.settings import HubSettings

router = APIRouter(prefix="/auth", tags=["auth"])

MIN_PASSWORD_LENGTH = 10


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


def _set_session_cookie(
    response: Response, token: str, settings: HubSettings, secure: bool
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.auth.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
    )


@router.post("/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[HubSettings, Depends(get_settings)],
) -> dict[str, object]:
    """Verify credentials and issue a session cookie plus bearer token."""
    client_address = actor_ip(request)
    throttle = LoginThrottle(session, settings.auth)
    # Reject a backed-off identity before touching credentials: the check keys
    # on the submitted name, so it cannot leak whether the account exists.
    throttle.check(body.username, client_address)
    user = (
        session.query(UserRecord)
        .filter(UserRecord.username == body.username)
        .one_or_none()
    )
    if (
        user is None
        or not user.is_active
        or not verify_password(body.password, user.password_hash)
    ):
        lock = throttle.register_failure(body.username, client_address)
        audit(
            session,
            actor_type="anonymous" if user is None else "user",
            actor_id=user.id if user else None,
            actor_name=body.username,
            action="auth.login_failed",
            ip=client_address,
            result="denied",
        )
        if lock is not None:
            audit(
                session,
                actor_type="anonymous" if user is None else "user",
                actor_id=user.id if user else None,
                actor_name=body.username,
                action="auth.login_locked",
                ip=client_address,
                result="denied",
            )
        session.commit()
        if lock is not None:
            raise lockout_error(lock)
        raise HubError(
            code="INVALID_CREDENTIALS",
            message="用户名或口令错误",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    service = AuthService(session, session_ttl_hours=settings.auth.session_ttl_hours)
    throttle.reset(body.username, client_address)
    _record, token = service.create_session(
        user.id, ip=client_address, user_agent=request.headers.get("user-agent")
    )
    audit(
        session,
        actor_type="user",
        actor_id=user.id,
        actor_name=user.username,
        action="auth.login",
        ip=client_address,
    )
    session.commit()
    _set_session_cookie(response, token, settings, request.url.scheme == "https")
    return {
        "token": token,
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


def _current_token(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        return token
    authorization = request.headers.get("authorization")
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    actor: Annotated[Actor, Depends(require_role("viewer"))],
    session: Annotated[Session, Depends(get_session)],
) -> Response:
    """Revoke the presented session token and clear the cookie."""
    token = _current_token(request)
    if token is not None and not token.startswith("hub_"):
        AuthService(session).revoke_session(sha256_hex(token))
        audit(
            session,
            actor_type="user",
            actor_id=actor.id,
            actor_name=actor.name,
            action="auth.logout",
            ip=actor_ip(request),
        )
        session.commit()
    response.delete_cookie(SESSION_COOKIE_NAME)
    return Response(status_code=204)


@router.get("/me")
def me(
    actor: Annotated[Actor, Depends(require_role("viewer"))],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Return the authenticated identity and its bootstrap password flag."""
    must_change_password = False
    if actor.kind == "user" and actor.id is not None:
        user = session.get(UserRecord, actor.id)
        must_change_password = bool(user.must_change_password) if user else False
    return {
        "actor_type": actor.kind,
        "id": actor.id,
        "username": actor.name,
        "role": actor.role,
        "must_change_password": must_change_password,
    }


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    actor: Annotated[Actor, Depends(require_role("viewer"))],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, object]:
    """Rotate the current user's password and clear the bootstrap flag."""
    if actor.kind != "user" or actor.id is None:
        raise HubError(
            code="FORBIDDEN",
            message="API Key 账号没有口令可修改",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    user = session.get(UserRecord, actor.id)
    if user is None or not verify_password(body.old_password, user.password_hash):
        raise HubError(
            code="INVALID_CREDENTIALS",
            message="旧口令错误",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    audit(
        session,
        actor_type=actor.kind,
        actor_id=actor.id,
        actor_name=actor.name,
        action="auth.change_password",
        resource_type="user",
        resource_id=str(actor.id),
        ip=None,
    )
    session.commit()
    return {"username": user.username, "must_change_password": False}
