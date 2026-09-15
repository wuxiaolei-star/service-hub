"""Quota enforcement for uploads and job creation."""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import ColumnElement, func
from sqlalchemy.orm import Session

from hub_server.dependencies_auth import Actor
from hub_server.errors import HubError, UploadTooLargeError
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


class UploadCap(NamedTuple):
    """The largest payload an actor may still upload, and the limit behind it.

    ``quota`` names the binding limit, or is ``None`` when only the configured
    per-request upload size binds. That distinction decides which stable error
    a rejection reports, so it must survive from the pre-check to the stream.
    """

    max_bytes: int
    quota: str | None
    used: int
    limit: int | None

    def error(self) -> HubError:
        """Build the stable client error for a payload this cap rejects."""
        if self.quota is None:
            return UploadTooLargeError(max_size_bytes=self.max_bytes)
        return _quota_exceeded(
            self.quota,
            self.used,
            self.limit if self.limit is not None else self.max_bytes,
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

    def upload_cap(self, actor: Actor, *, upload_max_bytes: int) -> UploadCap:
        """Return the largest payload this actor may still upload.

        Evaluated before the body is read, so an exhausted account is refused
        without spooling anything, and the result also bounds the stream so an
        undeclared (chunked) body cannot outgrow the remaining headroom.
        """
        if self._is_exempt(actor):
            return UploadCap(upload_max_bytes, None, 0, None)

        user = self._user(actor)
        per_user_count = user.quota_file_count if user is not None else None
        per_user_bytes = user.quota_total_bytes if user is not None else None

        if actor.kind == "user" and actor.id is not None:
            used_bytes, used_count = self._owner_usage(actor)
        else:
            used_bytes, used_count = 0, 0

        # One upload adds exactly one file, so an exhausted count limit means
        # this request may store nothing at all.
        if per_user_count is not None and used_count + 1 > per_user_count:
            return UploadCap(0, "file_count", used_count, per_user_count)

        candidates: list[UploadCap] = []
        if per_user_bytes is not None:
            candidates.append(
                UploadCap(
                    per_user_bytes - used_bytes, "total_bytes", used_bytes, per_user_bytes
                )
            )
        global_bytes = self._global_bytes()
        global_limit = self._settings.max_total_bytes
        candidates.append(
            UploadCap(global_limit - global_bytes, "global_bytes", global_bytes, global_limit)
        )

        binding = min(candidates, key=lambda candidate: candidate.max_bytes)
        if binding.max_bytes >= upload_max_bytes:
            return UploadCap(upload_max_bytes, None, 0, None)
        return UploadCap(
            max(binding.max_bytes, 0), binding.quota, binding.used, binding.limit
        )

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
            used_bytes, used_count = self._owner_usage(actor, exclude_file_id=exclude_file_id)
        else:
            used_bytes, used_count = 0, 0

        if per_user_count is not None and used_count + 1 > per_user_count:
            raise _quota_exceeded("file_count", used_count, per_user_count)
        if per_user_bytes is not None and used_bytes + incoming_bytes > per_user_bytes:
            raise _quota_exceeded("total_bytes", used_bytes, per_user_bytes)

        global_bytes = self._global_bytes(exclude_file_id=exclude_file_id)
        if global_bytes + incoming_bytes > self._settings.max_total_bytes:
            raise _quota_exceeded("global_bytes", global_bytes, self._settings.max_total_bytes)

    def _owner_usage(
        self, actor: Actor, *, exclude_file_id: int | None = None
    ) -> tuple[int, int]:
        """Return one actor's committed byte and file totals."""
        query = self._session.query(
            func.coalesce(func.sum(FileRecord.size_bytes), 0),
            func.count(FileRecord.id),
        )
        owner_condition = self._owner_filter(actor)
        if owner_condition is not None:
            query = query.filter(owner_condition)
        if exclude_file_id is not None:
            query = query.filter(FileRecord.id != exclude_file_id)
        used_bytes, used_count = query.one()
        return int(used_bytes), int(used_count)

    def _global_bytes(self, *, exclude_file_id: int | None = None) -> int:
        """Return committed storage for every owner."""
        query = self._session.query(func.coalesce(func.sum(FileRecord.size_bytes), 0))
        if exclude_file_id is not None:
            query = query.filter(FileRecord.id != exclude_file_id)
        return int(query.scalar() or 0)

    def usage(self, user_id: int) -> dict[str, int]:
        """Summarize one user's storage, file count, and active jobs."""
        used_bytes, file_count = (
            self._session.query(
                func.coalesce(func.sum(FileRecord.size_bytes), 0),
                func.count(FileRecord.id),
            )
            .filter(FileRecord.owner_user_id == user_id)
            .one()
        )
        active_jobs = (
            self._session.query(func.count(Job.id))
            .filter(
                Job.owner_user_id == user_id,
                Job.status.in_(ACTIVE_JOB_STATUSES),
            )
            .scalar()
        )
        return {
            "total_bytes": int(used_bytes or 0),
            "file_count": int(file_count or 0),
            "active_jobs": int(active_jobs or 0),
        }

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
