"""Failed-login throttling with exponential backoff."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from hub_server.errors import HubError
from hub_server.models import LoginAttemptRecord
from hub_server.settings import AuthSettings

_NO_CLIENT_ADDRESS = "-"
_MAX_BACKOFF_EXPONENT = 16


def _as_aware(value: datetime) -> datetime:
    """SQLite round-trips timestamps as naive UTC; compare in UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _bucket_ip(ip: str | None) -> str:
    """Store a placeholder for missing addresses so the unique key still holds."""
    return ip if ip else _NO_CLIENT_ADDRESS


def lockout_error(remaining: timedelta) -> HubError:
    """Build the stable refusal for a throttled login attempt."""
    seconds = max(1, math.ceil(remaining.total_seconds()))
    return HubError(
        code="TOO_MANY_ATTEMPTS",
        message="登录失败次数过多",
        status_code=429,
        details={"retry_after_seconds": seconds},
        # The standard header lets clients and proxies back off on their own.
        headers={"Retry-After": str(seconds)},
    )


class LoginThrottle:
    """Count failed logins per identity and refuse them during a backoff.

    The bucket key is (username, source address). One address attacking one
    account backs off without locking that account out for other addresses,
    and the refusal is keyed on the submitted name rather than a resolved
    user, so it never reveals whether the account exists.
    """

    def __init__(
        self,
        session: Session,
        settings: AuthSettings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._now = now if now is not None else (lambda: datetime.now(UTC))

    def check(self, username: str, ip: str | None) -> None:
        """Refuse the attempt while its bucket is still locked."""
        record = self._lookup(username, ip)
        if record is None or record.locked_until is None:
            return
        remaining = _as_aware(record.locked_until) - self._now()
        if remaining > timedelta():
            raise lockout_error(remaining)

    def register_failure(self, username: str, ip: str | None) -> timedelta | None:
        """Record one failure and return the lock it applied, if any.

        A failure that passes the threshold answers with a refusal straight
        away, so the threshold means "this many failures are tolerated" rather
        than leaving the offending attempt unthrottled.
        """
        now = self._now()
        self._prune(username, now)
        record = self._lookup(username, ip)
        if record is None:
            record = LoginAttemptRecord(username=username, ip=_bucket_ip(ip), failure_count=0)
            self._session.add(record)
        record.failure_count += 1
        record.last_failure_at = now
        delay = self._backoff(record.failure_count)
        if delay is None:
            return None
        record.locked_until = now + delay
        return delay

    def reset(self, username: str, ip: str | None) -> None:
        """Forget one bucket's failures after that identity logs in successfully."""
        record = self._lookup(username, ip)
        if record is not None:
            self._session.delete(record)

    def _backoff(self, failure_count: int) -> timedelta | None:
        """Double the lock after each failure past the threshold, up to the cap."""
        over = failure_count - self._settings.login_failure_threshold
        if over <= 0:
            return None
        doublings = min(over - 1, _MAX_BACKOFF_EXPONENT)
        seconds = self._settings.login_lockout_base_seconds << doublings
        return timedelta(seconds=min(seconds, self._settings.login_lockout_max_seconds))

    def _prune(self, username: str, now: datetime) -> None:
        """Drop stale buckets so repeated attempts cannot grow the table forever."""
        horizon = now - timedelta(seconds=self._settings.login_lockout_max_seconds)
        self._session.query(LoginAttemptRecord).filter(
            LoginAttemptRecord.username == username,
            LoginAttemptRecord.last_failure_at < horizon,
        ).delete(synchronize_session=False)

    def _lookup(self, username: str, ip: str | None) -> LoginAttemptRecord | None:
        return (
            self._session.query(LoginAttemptRecord)
            .filter(
                LoginAttemptRecord.username == username,
                LoginAttemptRecord.ip == _bucket_ip(ip),
            )
            .one_or_none()
        )
