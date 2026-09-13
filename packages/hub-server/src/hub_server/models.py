"""Persistent metadata models owned by the Hub."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hub_server.db import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _public_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


_SESSION_TTL = timedelta(hours=24)


def _session_expiry() -> datetime:
    return datetime.now(UTC) + _SESSION_TTL


class FileRecord(Base):
    """Metadata for a file held below the Hub-managed data directory."""

    __tablename__ = "file_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    scope: Mapped[str] = mapped_column(String(16), default="UPLOAD")
    role: Mapped[str] = mapped_column(String(16), default="INPUT")
    logical_name: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(String(1024), unique=True)
    extension: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="AVAILABLE")
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    job_files: Mapped[list[JobFile]] = relationship(back_populates="file_record")


class Plugin(Base):
    """Stable logical identity shared by all versions of a plugin."""

    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(256), nullable=True)
    author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    versions: Mapped[list[PluginVersion]] = relationship(back_populates="plugin")


class PluginVersion(Base):
    """Immutable source manifest and source hash for one business version."""

    __tablename__ = "plugin_versions"
    __table_args__ = (
        UniqueConstraint("plugin_id", "version", name="uq_plugin_versions_plugin_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[int] = mapped_column(ForeignKey("plugins.id", ondelete="RESTRICT"))
    version: Mapped[str] = mapped_column(String(64))
    spec_version: Mapped[str] = mapped_column(String(16))
    sdk_version: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="INSTALLED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    plugin: Mapped[Plugin] = relationship(back_populates="versions")
    builds: Mapped[list[PluginBuild]] = relationship(back_populates="plugin_version")


class PluginBuild(Base):
    """Immutable platform and runtime-specific build of a plugin version."""

    __tablename__ = "plugin_builds"
    __table_args__ = (
        UniqueConstraint(
            "plugin_version_id",
            "target_os",
            "target_arch",
            "runtime_type",
            name="uq_plugin_builds_version_platform_runtime",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    build_key: Mapped[str] = mapped_column(
        String(80), default=lambda: _public_id("plugin_build"), unique=True, index=True
    )
    manifest_build_id: Mapped[str] = mapped_column(String(256))
    plugin_version_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_versions.id", ondelete="RESTRICT")
    )
    target_os: Mapped[str] = mapped_column(String(32))
    target_arch: Mapped[str] = mapped_column(String(32))
    runtime_type: Mapped[str] = mapped_column(String(16), index=True)
    python_version: Mapped[str] = mapped_column(String(64))
    sdk_version: Mapped[str] = mapped_column(String(64))
    package_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    package_sha256: Mapped[str] = mapped_column(String(64))
    source_sha256: Mapped[str] = mapped_column(String(64))
    runtime_archive_path: Mapped[str] = mapped_column(String(1024))
    runtime_fingerprint: Mapped[str] = mapped_column(String(255))
    build_metadata_json: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="INSTALLING")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    plugin_version: Mapped[PluginVersion] = relationship(back_populates="builds")
    environment: Mapped[Environment | None] = relationship(
        back_populates="plugin_build", uselist=False
    )
    jobs: Mapped[list[Job]] = relationship(back_populates="plugin_build")
    operations: Mapped[list[RunnerOperation]] = relationship(back_populates="plugin_build")


class Environment(Base):
    """Installed runtime material for exactly one plugin build."""

    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(primary_key=True)
    plugin_build_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_builds.id", ondelete="RESTRICT"), unique=True
    )
    runtime_type: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str] = mapped_column(String(255))
    environment_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="INSTALLING")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    healthy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plugin_build: Mapped[PluginBuild] = relationship(back_populates="environment")


class Job(Base):
    """One immutable request snapshot and its mutable execution lifecycle."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(
        String(80), default=lambda: _public_id("job"), unique=True, index=True
    )
    plugin_build_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_builds.id", ondelete="RESTRICT")
    )
    runtime_type: Mapped[str] = mapped_column(String(16))
    runtime_fingerprint: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    params_json: Mapped[dict[str, object]] = mapped_column(JSON)
    inputs_json: Mapped[dict[str, object]] = mapped_column(JSON)
    job_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    timeout_seconds: Mapped[int]
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pipeline_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plugin_build: Mapped[PluginBuild] = relationship(back_populates="jobs")
    files: Mapped[list[JobFile]] = relationship(back_populates="job")


class JobFile(Base):
    """Named Job input/output association with an immutable file snapshot."""

    __tablename__ = "job_files"
    __table_args__ = (
        UniqueConstraint("job_id", "role", "logical_name", name="uq_job_files_role_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    file_record_id: Mapped[int] = mapped_column(
        ForeignKey("file_records.id", ondelete="RESTRICT")
    )
    role: Mapped[str] = mapped_column(String(16))
    logical_name: Mapped[str] = mapped_column(String(255))
    file_snapshot_json: Mapped[dict[str, object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    job: Mapped[Job] = relationship(back_populates="files")
    file_record: Mapped[FileRecord] = relationship(back_populates="job_files")


class RunnerOperation(Base):
    """Runtime-specific installation work atomically claimed by a runner."""

    __tablename__ = "runner_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_key: Mapped[str] = mapped_column(
        String(80), default=lambda: _public_id("operation"), unique=True, index=True
    )
    plugin_build_id: Mapped[int] = mapped_column(
        ForeignKey("plugin_builds.id", ondelete="RESTRICT")
    )
    runtime_type: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    payload_json: Mapped[dict[str, object]] = mapped_column(JSON)
    timeout_seconds: Mapped[int | None] = mapped_column(nullable=True)
    exit_code: Mapped[int | None] = mapped_column(nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plugin_build: Mapped[PluginBuild] = relationship(back_populates="operations")


class UserRecord(Base):
    """A human operator that can authenticate against the Hub."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer', 'operator', 'publisher', 'admin')",
            name="ck_users_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    quota_total_bytes: Mapped[int | None] = mapped_column(nullable=True)
    quota_file_count: Mapped[int | None] = mapped_column(nullable=True)
    quota_concurrent_jobs: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    sessions: Mapped[list[SessionRecord]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SessionRecord(Base):
    """A revocable bearer-token session issued by the authentication layer."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_session_expiry
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[UserRecord] = relationship(back_populates="sessions")


class ApiKeyRecord(Base):
    """A machine account credential for business systems and hubctl."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="operator")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class JobCallback(Base):
    """Webhook delivery state machine for one Job terminal notification."""

    __tablename__ = "job_callbacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1024))
    secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    last_status_code: Mapped[int | None] = mapped_column(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


class Schedule(Base):
    """Periodic job creation rule evaluated by the scheduler process."""

    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    plugin_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(32))
    runtime_type: Mapped[str] = mapped_column(String(16))
    inputs_json: Mapped[dict[str, object]] = mapped_column(JSON)
    params_json: Mapped[dict[str, object]] = mapped_column(JSON)
    interval_minutes: Mapped[int] = mapped_column()
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )


class Pipeline(Base):
    """A linear chain of job steps with $prev output mapping."""

    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    steps_json: Mapped[list[object]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    runs: Mapped[list[PipelineRun]] = relationship(back_populates="pipeline")


class PipelineRun(Base):
    """One execution of a pipeline; steps advance only on SUCCESS."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_id: Mapped[int] = mapped_column(
        ForeignKey("pipelines.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), default="RUNNING", index=True)
    current_step: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, onupdate=_utc_now
    )
    pipeline: Mapped[Pipeline] = relationship(back_populates="runs")


class AuditLogRecord(Base):
    """One audited operation performed against the Hub."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, index=True
    )
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[int | None] = mapped_column(nullable=True)
    actor_name: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="ok")
