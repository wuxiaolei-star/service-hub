"""User and API key management endpoints (admin-gated)."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from hub_server.dependencies import get_session
from hub_server.dependencies_auth import require_role
from hub_server.errors import HubError
from hub_server.models import ApiKeyRecord, UserRecord
from hub_server.services.audit import record as audit
from hub_server.services.auth import AuthService, hash_password

router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[Depends(require_role("admin"))]
)
key_router = APIRouter(
    prefix="/api-keys", tags=["api-keys"], dependencies=[Depends(require_role("operator"))]
)

MIN_PASSWORD_LENGTH = 10


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: str = Field(pattern="^(viewer|operator|publisher|admin)$")


class UserResponse(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    must_change_password: bool


def _user_response(user: UserRecord) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
    }


def _get_user(session: Session, user_id: int) -> UserRecord:
    user = session.get(UserRecord, user_id)
    if user is None:
        raise HubError(code="USER_NOT_FOUND", message="用户不存在", status_code=404)
    return user


@router.get("")
def list_users(session: Annotated[Session, Depends(get_session)]) -> dict[str, object]:
    users = session.query(UserRecord).order_by(UserRecord.id).all()
    return {"items": [_user_response(user) for user in users]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    if session.query(UserRecord).filter(UserRecord.username == body.username).one_or_none():
        raise HubError(
            code="USERNAME_TAKEN", message="用户名已存在", status_code=status.HTTP_409_CONFLICT
        )
    user = UserRecord(
        username=body.username, password_hash=hash_password(body.password), role=body.role
    )
    session.add(user)
    session.flush()
    audit(
        session,
        actor_type="user",
        actor_id=None,
        actor_name="admin",
        action="user.create",
        resource_type="user",
        resource_id=str(user.id),
        detail={"username": user.username, "role": user.role},
    )
    session.commit()
    return _user_response(user)


@router.post("/{user_id}/disable")
def disable_user(
    user_id: int, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    user = _get_user(session, user_id)
    user.is_active = False
    audit(
        session,
        actor_type="user",
        actor_id=None,
        actor_name="admin",
        action="user.disable",
        resource_type="user",
        resource_id=str(user.id),
    )
    session.commit()
    return _user_response(user)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    """Reset one user's password and return the one-time plaintext."""
    user = _get_user(session, user_id)
    password = secrets.token_urlsafe(18)
    user.password_hash = hash_password(password)
    user.must_change_password = True
    audit(
        session,
        actor_type="user",
        actor_id=None,
        actor_name="admin",
        action="user.reset_password",
        resource_type="user",
        resource_id=str(user.id),
    )
    session.commit()
    return {"username": user.username, "password": password, "must_change_password": True}


class CreateApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    role: str = Field(pattern="^(viewer|operator|publisher|admin)$")


@key_router.get("")
def list_api_keys(
    session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    keys = session.query(ApiKeyRecord).order_by(ApiKeyRecord.id).all()
    return {
        "items": [
            {
                "id": key.id,
                "name": key.name,
                "key_prefix": key.key_prefix,
                "role": key.role,
                "revoked": key.revoked_at is not None,
                "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
            }
            for key in keys
        ]
    }


@key_router.post("", status_code=status.HTTP_201_CREATED)
def create_api_key(
    body: CreateApiKeyRequest, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    service = AuthService(session)
    record, plaintext = service.create_api_key(name=body.name, role=body.role)
    audit(
        session,
        actor_type="user",
        actor_id=None,
        actor_name="admin",
        action="api_key.create",
        resource_type="api_key",
        resource_id=str(record.id),
        detail={"name": record.name, "role": record.role},
    )
    session.commit()
    return {
        "id": record.id,
        "name": record.name,
        "key_prefix": record.key_prefix,
        "role": record.role,
        "key": plaintext,
    }


@key_router.post("/{key_id}/revoke")
def revoke_api_key(
    key_id: int, session: Annotated[Session, Depends(get_session)]
) -> dict[str, object]:
    service = AuthService(session)
    record = session.get(ApiKeyRecord, key_id)
    if record is None:
        raise HubError(code="API_KEY_NOT_FOUND", message="API Key 不存在", status_code=404)
    service.revoke_api_key(key_id)
    audit(
        session,
        actor_type="user",
        actor_id=None,
        actor_name="admin",
        action="api_key.revoke",
        resource_type="api_key",
        resource_id=str(record.id),
    )
    session.commit()
    return {"id": record.id, "name": record.name, "revoked": True}
