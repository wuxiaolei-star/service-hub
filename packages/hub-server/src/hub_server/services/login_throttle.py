"""Failed-login throttling with exponential backoff and a per-address spray cap."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

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

    Two layers of buckets guard the login endpoint:

    * ``(username, source address)`` buckets with exponential backoff. One
      address attacking one account backs off without locking that account out
      for other addresses, and the refusal is keyed on the submitted name rather
      than a resolved user, so it never reveals whether the account exists.
    * A per-address aggregate over a sliding window (audit M-7): password
      spraying across many usernames from one address would otherwise stay
      under every per-identity threshold, so once the window accumulates
      ``login_ip_failure_threshold`` failures the whole address is refused until
      the earliest in-window failure ages out of it.

    The counters live in the ``login_attempts`` table; ``reset`` after a
    successful login clears only that identity's own row, never the address
    aggregate.
    """

    # Table hygiene (audit L-12): ``_prune`` only drops rows of the username
    # currently failing, so rows of one-off names would accumulate forever. A
    # process-wide counter fires the horizon-wide delete once every
    # ``login_prune_interval_attempts`` login attempts, on the login path.
    _prune_lock: ClassVar[threading.Lock] = threading.Lock()
    _attempts_since_prune: ClassVar[int] = 0

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
        """Refuse the attempt while its bucket or address is still locked."""
        self._maybe_prune_all()
        record = self._lookup(username, ip)
        if record is not None and record.locked_until is not None:
            remaining = _as_aware(record.locked_until) - self._now()
            if remaining > timedelta():
                raise lockout_error(remaining)
        spray_remaining = self._ip_window_remaining(ip)
        if spray_remaining is not None:
            raise lockout_error(spray_remaining)

    def register_failure(self, username: str, ip: str | None) -> timedelta | None:
        """Record one failure and return the lock it applied, if any.

        A failure that passes either threshold answers with a refusal straight
        away, so a threshold means "this many failures are tolerated" rather
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
        if delay is not None:
            record.locked_until = now + delay
            return delay
        # Still inside the per-identity threshold; the address-wide spray guard
        # may still refuse this very attempt.
        return self._ip_window_remaining(ip)

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

    def _ip_window_rows(
        self, ip: str | None, horizon: datetime
    ) -> Query[LoginAttemptRecord]:
        """Base query for one address's rows with failures inside the window."""
        return self._session.query(LoginAttemptRecord).filter(
            LoginAttemptRecord.ip == _bucket_ip(ip),
            LoginAttemptRecord.last_failure_at >= horizon,
        )

    def _ip_window_remaining(self, ip: str | None) -> timedelta | None:
        """Refusal duration while the address sits at the spray threshold."""
        window = timedelta(seconds=self._settings.login_ip_window_seconds)
        now = self._now()
        horizon = now - window
        rows = self._ip_window_rows(ip, horizon)
        total = rows.with_entities(
            func.coalesce(func.sum(LoginAttemptRecord.failure_count), 0)
        ).scalar()
        if int(total or 0) < self._settings.login_ip_failure_threshold:
            return None
        # Refuse until the earliest in-window failure ages out of the window;
        # the exact lift moment is re-derived on every attempt, so the hint can
        # only err on the short side while fresh failures keep arriving.
        oldest = rows.with_entities(func.min(LoginAttemptRecord.last_failure_at)).scalar()
        if oldest is None:  # pragma: no cover - the total above requires a row
            return None
        remaining = _as_aware(oldest) + window - now
        return remaining if remaining > timedelta() else None

    def _maybe_prune_all(self) -> None:
        """Run the horizon-wide cleanup every N login attempts (audit L-12)."""
        with LoginThrottle._prune_lock:
            LoginThrottle._attempts_since_prune += 1
            due = (
                LoginThrottle._attempts_since_prune
                >= self._settings.login_prune_interval_attempts
            )
            if due:
                LoginThrottle._attempts_since_prune = 0
        if due:
            horizon = self._now() - timedelta(seconds=self._settings.login_lockout_max_seconds)
            self._session.query(LoginAttemptRecord).filter(
                LoginAttemptRecord.last_failure_at < horizon
            ).delete(synchronize_session=False)

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
