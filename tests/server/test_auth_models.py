from __future__ import annotations

import pytest
from hub_server.models import ApiKeyRecord, AuditLogRecord, SessionRecord, UserRecord
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_user_roundtrip_with_role(session: Session) -> None:
    user = UserRecord(username="admin", password_hash="argon2$hash", role="admin")
    session.add(user)
    session.commit()

    loaded = session.query(UserRecord).one()
    assert loaded.username == "admin"
    assert loaded.role == "admin"
    assert loaded.is_active is True
    assert loaded.must_change_password is False
    assert loaded.created_at is not None


def test_username_must_be_unique(session: Session) -> None:
    session.add(UserRecord(username="dup", password_hash="h", role="viewer"))
    session.commit()
    session.add(UserRecord(username="dup", password_hash="h", role="viewer"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_user_role_rejects_unknown_values(session: Session) -> None:
    user = UserRecord(username="bad", password_hash="h", role="superuser")
    session.add(user)
    with pytest.raises(IntegrityError):
        session.commit()


def test_api_key_roundtrip_and_prefix(session: Session) -> None:
    key = ApiKeyRecord(
        name="业务系统", key_prefix="hub_abcd1234", key_hash="0" * 64, role="operator"
    )
    session.add(key)
    session.commit()

    loaded = session.query(ApiKeyRecord).one()
    assert loaded.key_prefix == "hub_abcd1234"
    assert loaded.revoked_at is None
    assert loaded.last_used_at is None


def test_session_roundtrip(session: Session) -> None:
    user = UserRecord(username="op", password_hash="h", role="operator")
    session.add(user)
    session.flush()
    record = SessionRecord(
        token_hash="a" * 64,
        user_id=user.id,
        ip="127.0.0.1",
        user_agent="hubctl",
    )
    session.add(record)
    session.commit()

    loaded = session.query(SessionRecord).one()
    assert loaded.user_id == user.id
    assert loaded.revoked_at is None
    assert loaded.expires_at is not None


def test_session_cascade_deletes_with_user(session: Session) -> None:
    user = UserRecord(username="gone", password_hash="h", role="viewer")
    session.add(user)
    session.flush()
    session.add(SessionRecord(token_hash="b" * 64, user_id=user.id))
    session.commit()

    session.delete(user)
    session.commit()
    assert session.query(SessionRecord).count() == 0


def test_audit_log_roundtrip(session: Session) -> None:
    entry = AuditLogRecord(
        actor_type="user",
        actor_id=1,
        actor_name="admin",
        action="job.cancel",
        resource_type="job",
        resource_id="job_123",
        detail={"reason": "手动取消"},
        ip="127.0.0.1",
        result="ok",
    )
    session.add(entry)
    session.commit()

    loaded = session.query(AuditLogRecord).one()
    assert loaded.action == "job.cancel"
    assert loaded.detail == {"reason": "手动取消"}
    assert loaded.at is not None
