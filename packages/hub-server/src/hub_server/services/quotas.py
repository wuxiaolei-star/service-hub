"""Quota enforcement for uploads and job creation."""

from __future__ import annotations

from sqlalchemy import ColumnElement, func
from sqlalchemy.orm import Session

from hub_server.dependencies_auth import Actor
from hub_server.errors import HubError
from hub_server.models import FileRecord, Job, UserRecord
from hub_server.settings import QuotasSettings

ACTIVE_JOB_STATUSES = ("PENDING", "PREPARING", "RUNNING", "CANCEL_REQUESTED")


def _quota_exceeded(quota: str, used: int, limit: int) -> HubError:
    return HubError(
        code="QUOTA_EXCEEDED",
        message="超出资源配额限制",
        status_code=409,
        details={"quota": quota, "used": used, "limit": limit},
    )


class QuotaService:
    """Check storage and concurrency limits for one request actor."""

    def __init__(self, session: Session, settings: QuotasSettings) -> None:
        self._session = session
        self._settings = settings

    def _is_exempt(self, actor: Actor) -> bool:
        if not self._settings.enabled:
            return True
        return actor.kind == "anonymous" or actor.role == "admin"

    def _user(self, actor: Actor) -> UserRecord | None:
        if actor.kind != "user" or actor.id is None:
            return None
        return self._session.get(UserRecord, actor.id)

    def _owner_filter(self, actor: Actor) -> ColumnElement[bool] | None:
        if actor.kind == "user" and actor.id is not None:
            return FileRecord.owner_user_id == actor.id
        return None

    def enforce_upload(
        self, actor: Actor, incoming_bytes: int, *, exclude_file_id: int | None = None
    ) -> None:
        """Reject uploads that would exceed file count or storage limits."""
        if self._is_exempt(actor):
            return

        user = self._user(actor)
        per_user_count = user.quota_file_count if user is not None else None
        per_user_bytes = user.quota_total_bytes if user is not None else None

        if actor.kind == "user" and actor.id is not None:
            owner_query = self._session.query(
                func.coalesce(func.sum(FileRecord.size_bytes), 0),
                func.count(FileRecord.id),
            )
            owner_condition = self._owner_filter(actor)
            if owner_condition is not None:
                owner_query = owner_query.filter(owner_condition)
            if exclude_file_id is not None:
                owner_query = owner_query.filter(FileRecord.id != exclude_file_id)
            used_bytes, used_count = owner_query.one()
        else:
            used_bytes, used_count = 0, 0

        if per_user_count is not None and used_count + 1 > per_user_count:
            raise _quota_exceeded("file_count", used_count, per_user_count)
        if per_user_bytes is not None and used_bytes + incoming_bytes > per_user_bytes:
            raise _quota_exceeded("total_bytes", used_bytes, per_user_bytes)

        global_query = self._session.query(
            func.coalesce(func.sum(FileRecord.size_bytes), 0)
        )
        if exclude_file_id is not None:
            global_query = global_query.filter(FileRecord.id != exclude_file_id)
        global_bytes = global_query.scalar()
        global_limit = self._settings.max_total_bytes
        if global_bytes is not None and global_bytes + incoming_bytes > global_limit:
            raise _quota_exceeded("global_bytes", global_bytes, global_limit)

    def enforce_job_creation(self, actor: Actor) -> None:
        """Reject job creation beyond the user's concurrent-job limit."""
        if self._is_exempt(actor):
            return
        user = self._user(actor)
        if user is None or actor.id is None:
            return
        limit = user.quota_concurrent_jobs
        if limit is None:
            limit = self._settings.max_concurrent_jobs
        active = (
            self._session.query(func.count(Job.id))
            .filter(
                Job.owner_user_id == actor.id,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
            .scalar()
        )
        if active is not None and active + 1 > limit:
            raise _quota_exceeded("concurrent_jobs", active, limit)
