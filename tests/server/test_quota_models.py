"""Quota columns and file ownership model tests for V2.1."""

from __future__ import annotations

import pytest
from hub_server.models import FileRecord, UserRecord
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def make_user(session: Session, username: str = "owner") -> UserRecord:
    user = UserRecord(
        username=username,
        password_hash="h",
        role="operator",
        quota_total_bytes=2048,
        quota_file_count=5,
        quota_concurrent_jobs=2,
    )
    session.add(user)
    session.commit()
    return user


def test_user_quota_override_columns_roundtrip(session: Session) -> None:
    user = make_user(session)
    loaded = session.get(UserRecord, user.id)
    assert loaded.quota_total_bytes == 2048
    assert loaded.quota_file_count == 5
    assert loaded.quota_concurrent_jobs == 2


def test_user_quota_overrides_default_to_null(session: Session) -> None:
    user = UserRecord(username="plain", password_hash="h", role="viewer")
    session.add(user)
    session.commit()
    loaded = session.get(UserRecord, user.id)
    assert loaded.quota_total_bytes is None
    assert loaded.quota_file_count is None
    assert loaded.quota_concurrent_jobs is None


def test_file_record_accepts_owner(session: Session) -> None:
    user = make_user(session)
    record = FileRecord(
        file_key="file_x",
        logical_name="x.nc",
        original_filename="x.nc",
        relative_path="files/x.nc",
        size_bytes=10,
        sha256="a" * 64,
        owner_user_id=user.id,
    )
    session.add(record)
    session.commit()
    loaded = session.get(FileRecord, record.id)
    assert loaded.owner_user_id == user.id


def test_file_record_owner_must_reference_existing_user(session: Session) -> None:
    record = FileRecord(
        file_key="file_y",
        logical_name="y.nc",
        original_filename="y.nc",
        relative_path="files/y.nc",
        size_bytes=10,
        sha256="b" * 64,
        owner_user_id=9999,
    )
    session.add(record)
    with pytest.raises(IntegrityError):
        session.commit()
