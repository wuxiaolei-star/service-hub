from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hub_server.models import UserRecord
from hub_server.services.auth import (
    AuthService,
    generate_token,
    hash_password,
    sha256_hex,
    verify_password,
)
from sqlalchemy.orm import Session


def make_user(session: Session) -> UserRecord:
    user = UserRecord(username="admin", password_hash=hash_password("s3cret-pass"), role="admin")
    session.add(user)
    session.commit()
    return user


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret-pass")
    assert hashed != "s3cret-pass"
    assert hashed.startswith("$argon2")
    assert verify_password("s3cret-pass", hashed) is True
    assert verify_password("wrong-pass", hashed) is False


def test_generate_token_is_high_entropy_and_hash_is_stable() -> None:
    first = generate_token()
    second = generate_token()
    assert first != second
    assert len(first) >= 40
    assert sha256_hex(first) == sha256_hex(first)
    assert sha256_hex(first) != sha256_hex(second)
    assert len(sha256_hex(first)) == 64


def test_create_and_resolve_session(session: Session) -> None:
    user = make_user(session)
    service = AuthService(session)

    _record, token = service.create_session(user.id, ip="127.0.0.1", user_agent="hubctl")
    session.commit()

    assert token.startswith(("hub_", "")) and len(token) >= 40
    resolved = service.resolve_session(token)
    assert resolved is not None
    assert resolved.user_id == user.id
    assert resolved.revoked_at is None


def test_resolve_session_rejects_unknown_or_revoked_or_expired(session: Session) -> None:
    user = make_user(session)
    service = AuthService(session)
    record, token = service.create_session(user.id)
    session.commit()

    assert service.resolve_session("not-a-real-token") is None

    service.revoke_session(record.token_hash)
    session.commit()
    assert service.resolve_session(token) is None


def test_resolve_session_rejects_when_user_is_deactivated(session: Session) -> None:
    user = make_user(session)
    service = AuthService(session)
    _record, token = service.create_session(user.id)
    session.commit()

    user.is_active = False
    session.commit()
    assert service.resolve_session(token) is None


def test_expired_session_is_rejected(session: Session) -> None:
    user = make_user(session)
    service = AuthService(session)
    record, token = service.create_session(user.id)
    record.expires_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()

    assert service.resolve_session(token) is None


def test_api_key_lifecycle(session: Session) -> None:
    service = AuthService(session)
    record, plaintext = service.create_api_key(name="业务系统", role="operator")
    session.commit()

    assert plaintext.startswith("hub_")
    assert len(plaintext) >= 40
    assert record.key_prefix == plaintext[:12]
    assert record.key_hash != plaintext

    resolved = service.resolve_api_key(plaintext)
    assert resolved is not None
    assert resolved.id == record.id
    assert resolved.last_used_at is not None

    service.revoke_api_key(record.id)
    session.commit()
    assert service.resolve_api_key(plaintext) is None


def test_api_key_role_must_be_valid(session: Session) -> None:
    service = AuthService(session)
    with pytest.raises(ValueError, match="role"):
        service.create_api_key(name="bad", role="superuser")
