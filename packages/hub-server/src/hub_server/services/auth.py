"""Credential hashing, session, and API key services for the Hub."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from hub_server.models import ApiKeyRecord, SessionRecord

VALID_API_KEY_ROLES = {"viewer", "operator", "publisher", "admin"}
KEY_PREFIX_LENGTH = 12


_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Return whether the plaintext matches the stored argon2 hash."""
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def generate_token() -> str:
    """Generate a 256-bit URL-safe bearer token."""
    return secrets.token_urlsafe(32)


def sha256_hex(value: str) -> str:
    """Return the SHA256 hex digest of a credential for at-rest storage."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_aware(value: datetime) -> datetime:
    """SQLite round-trips timestamps as naive UTC; compare in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class AuthService:
    """Create and resolve revocable sessions and API keys."""

    def __init__(self, session: Session, *, session_ttl_hours: int = 24) -> None:
        self._session = session
        self._ttl = timedelta(hours=session_ttl_hours)

    def create_session(
        self,
        user_id: int,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[SessionRecord, str]:
        """Issue one session and return its record with the plaintext token."""
        token = generate_token()
        record = SessionRecord(
            token_hash=sha256_hex(token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + self._ttl,
            ip=ip,
            user_agent=user_agent,
        )
        self._session.add(record)
        self._session.flush()
        return record, token

    def resolve_session(self, token: str) -> SessionRecord | None:
        """Resolve a plaintext token to an active, unexpired session."""
        record = (
            self._session.query(SessionRecord)
            .filter(SessionRecord.token_hash == sha256_hex(token))
            .one_or_none()
        )
        if record is None:
            return None
        if record.revoked_at is not None or _as_aware(record.expires_at) <= datetime.now(UTC):
            return None
        if not record.user.is_active:
            return None
        return record

    def revoke_session(self, token_hash: str) -> None:
        """Revoke one session by its stored hash."""
        record = (
            self._session.query(SessionRecord)
            .filter(SessionRecord.token_hash == token_hash)
            .one_or_none()
        )
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            self._session.flush()

    def create_api_key(self, *, name: str, role: str) -> tuple[ApiKeyRecord, str]:
        """Issue one API key and return its record with the one-time plaintext."""
        if role not in VALID_API_KEY_ROLES:
            raise ValueError(f"api key role must be one of {sorted(VALID_API_KEY_ROLES)}")
        plaintext = f"hub_{generate_token()}"
        record = ApiKeyRecord(
            name=name,
            key_prefix=plaintext[:KEY_PREFIX_LENGTH],
            key_hash=sha256_hex(plaintext),
            role=role,
        )
        self._session.add(record)
        self._session.flush()
        return record, plaintext

    def resolve_api_key(self, plaintext: str) -> ApiKeyRecord | None:
        """Resolve a plaintext API key and touch its last-used timestamp."""
        record = (
            self._session.query(ApiKeyRecord)
            .filter(ApiKeyRecord.key_hash == sha256_hex(plaintext))
            .one_or_none()
        )
        if record is None or record.revoked_at is not None:
            return None
        record.last_used_at = datetime.now(UTC)
        self._session.flush()
        return record

    def revoke_api_key(self, key_id: int) -> None:
        """Revoke one API key by id."""
        record = self._session.get(ApiKeyRecord, key_id)
        if record is not None and record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            self._session.flush()
