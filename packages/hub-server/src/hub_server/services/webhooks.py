"""Webhook callback persistence and terminal-notification delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from hub_server.errors import HubError, WebhookUrlForbiddenError
from hub_server.models import Job, JobCallback, WebhookDelivery
from hub_server.services.audit import record as audit
from hub_server.services.webhook_guard import (
    ensure_delivery_target_allowed,
    normalize_webhook_url,
)
from hub_server.settings import WebhooksSettings

TERMINAL_JOB_STATUSES: tuple[str, ...] = ("SUCCESS", "FAILED", "CANCELLED", "TIMED_OUT")
RETRYABLE_STATES: tuple[str, ...] = ("PENDING", "FAILED")
REPLAYABLE_STATES: tuple[str, ...] = ("FAILED", "EXHAUSTED")
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 60
DELIVERY_TIMEOUT_SECONDS = 10


def enqueue_callback(
    session: Session,
    job_id: int,
    url: str,
    secret: str | None = None,
    *,
    policy: WebhooksSettings | None = None,
) -> JobCallback:
    """Register one PENDING callback for a Job; the caller owns the commit.

    The URL passes the SSRF guard here, so a forbidden target fails the enclosing
    request instead of being persisted and dialed later by the scheduler.
    """
    callback = JobCallback(
        job_id=job_id,
        url=normalize_webhook_url(url, policy),
        secret=secret,
        state="PENDING",
    )
    session.add(callback)
    session.flush()
    return callback


def replay_callback(
    session: Session,
    callback_id: int,
    now: datetime | None = None,
) -> JobCallback:
    """Reset one FAILED or EXHAUSTED callback so the scheduler delivers it again.

    A PENDING callback is still in flight and a SUCCEEDED callback already
    reached its target, so only FAILED and EXHAUSTED states are replayable.
    The reset clears every delivery artifact (attempts, backoff schedule, last
    response) so the next :func:`deliver_due` scan treats the callback as fresh.
    The caller owns the commit; user attribution is recorded by the router,
    which is the only layer that knows the acting subject.
    """
    del now  # accepted for call-site symmetry with deliver_due; replay is immediate
    callback = session.get(JobCallback, callback_id)
    if callback is None:
        raise HubError(code="CALLBACK_NOT_FOUND", message="回调不存在", status_code=404)
    if callback.state not in REPLAYABLE_STATES:
        raise HubError(
            code="CALLBACK_NOT_REPLAYABLE",
            message="回调当前状态不可重放",
            status_code=409,
        )
    callback.state = "PENDING"
    callback.attempts = 0
    callback.next_attempt_at = None
    callback.last_status_code = None
    callback.last_error = None
    session.flush()
    return callback


def deliver_due(
    session: Session,
    now: datetime | None = None,
    *,
    policy: WebhooksSettings | None = None,
) -> int:
    """Deliver every due callback whose Job reached a terminal status.

    A callback is due when its state is PENDING or FAILED and its optional
    next_attempt_at has passed. Each delivery posts one JSON body describing the
    Job outcome: a 2xx response marks the callback SUCCEEDED while any transport
    failure, non-2xx response, or SSRF-guard refusal increments attempts, schedules
    a linear backoff (next_attempt_at = now + 60s * attempts) and moves the callback
    to EXHAUSTED once three attempts have been made. Every delivery writes one
    "webhook.deliver" audit entry attributed to the system scheduler. Each callback
    commits immediately after delivery so a crash between deliveries cannot lose
    the attempts/audit history of callbacks already processed; returns the number
    of processed callbacks.
    """
    current = _utc_now(now)
    rows = session.execute(
        select(JobCallback, Job)
        .join(Job, JobCallback.job_id == Job.id)
        .where(
            JobCallback.state.in_(RETRYABLE_STATES),
            Job.status.in_(TERMINAL_JOB_STATUSES),
            or_(JobCallback.next_attempt_at.is_(None), JobCallback.next_attempt_at <= current),
        )
        .order_by(JobCallback.id)
    ).all()
    processed = 0
    for callback, job in rows:
        _deliver_one(session, callback, job, current, policy)
        session.commit()
        processed += 1
    return processed


def _deliver_one(
    session: Session,
    callback: JobCallback,
    job: Job,
    now: datetime,
    policy: WebhooksSettings | None,
) -> None:
    """Attempt one delivery, record its history row, and advance the state machine."""
    body = json.dumps(
        {
            "job_id": job.job_key,
            "status": job.status,
            "error_summary": job.error_summary,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if callback.secret:
        digest = hmac.new(callback.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature"] = f"sha256={digest}"

    status_code: int | None = None
    error: str | None = None
    result = "ok"
    try:
        ensure_delivery_target_allowed(callback.url, policy)
    except WebhookUrlForbiddenError as refusal:
        error = refusal.message
        result = "denied"
    else:
        try:
            status_code = _post_json(callback.url, body, headers)
            if not 200 <= status_code < 300:
                error = f"HTTP {status_code}"
                result = "denied"
        except urllib.error.HTTPError as http_error:
            status_code = http_error.code
            error = f"HTTP {http_error.code}"
            result = "denied"
        except Exception as transport_error:  # any transport failure must be retried
            status_code = None
            error = str(transport_error) or type(transport_error).__name__
            result = "denied"

    callback.last_status_code = status_code
    callback.last_error = error
    if result == "denied":
        callback.attempts += 1
        if callback.attempts >= MAX_ATTEMPTS:
            callback.state = "EXHAUSTED"
        else:
            callback.state = "FAILED"
            callback.next_attempt_at = now + timedelta(
                seconds=RETRY_BACKOFF_SECONDS * callback.attempts
            )
    else:
        callback.state = "SUCCEEDED"

    session.add(
        WebhookDelivery(
            callback_id=callback.id,
            attempted_at=now,
            status_code=status_code,
            ok=result == "ok",
            error=error,
        )
    )

    audit(
        session,
        actor_type="system",
        actor_id=None,
        actor_name="scheduler",
        action="webhook.deliver",
        resource_type="job_callback",
        resource_id=str(callback.id),
        detail={
            "url": callback.url,
            "attempts": callback.attempts,
            "status_code": status_code,
            "job_id": job.job_key,
            "error": error,
        },
        result=result,
    )


def _post_json(url: str, body: bytes, headers: dict[str, str]) -> int:
    """POST one JSON body and return the HTTP status code.

    Kept as a module-level seam so tests can monkeypatch delivery without any
    network access; non-2xx responses surface as urllib HTTPError.
    """
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=DELIVERY_TIMEOUT_SECONDS) as response:
        return int(response.status)


def _utc_now(now: datetime | None) -> datetime:
    """Interpret a missing or naive evaluation time as the current UTC time."""
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)
