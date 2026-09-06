"""Create immutable plugin, environment, Job, and runner operation metadata.

Revision ID: 0002_plugins_jobs
Revises: 0001_file_records
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_plugins_jobs"
down_revision: str | None = "0001_file_records"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column[sa.DateTime], sa.Column[sa.DateTime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    """Create the immutable build graph and runtime work queues."""
    op.create_table(
        "plugins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plugin_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=256), nullable=True),
        sa.Column("author", sa.String(length=256), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plugins_plugin_key", "plugins", ["plugin_key"], unique=True)

    op.create_table(
        "plugin_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plugin_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("spec_version", sa.String(length=16), nullable=False),
        sa.Column("sdk_version", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="INSTALLED"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["plugin_id"], ["plugins.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_id", "version", name="uq_plugin_versions_plugin_version"
        ),
    )

    op.create_table(
        "plugin_builds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("build_key", sa.String(length=80), nullable=False),
        sa.Column("manifest_build_id", sa.String(length=256), nullable=False),
        sa.Column("plugin_version_id", sa.Integer(), nullable=False),
        sa.Column("target_os", sa.String(length=32), nullable=False),
        sa.Column("target_arch", sa.String(length=32), nullable=False),
        sa.Column("runtime_type", sa.String(length=16), nullable=False),
        sa.Column("python_version", sa.String(length=64), nullable=False),
        sa.Column("sdk_version", sa.String(length=64), nullable=False),
        sa.Column("package_path", sa.String(length=1024), nullable=True),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("runtime_archive_path", sa.String(length=1024), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(length=255), nullable=False),
        sa.Column("build_metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="INSTALLING"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["plugin_version_id"], ["plugin_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plugin_version_id",
            "target_os",
            "target_arch",
            "runtime_type",
            name="uq_plugin_builds_version_platform_runtime",
        ),
    )
    op.create_index("ix_plugin_builds_build_key", "plugin_builds", ["build_key"], unique=True)
    op.create_index(
        "ix_plugin_builds_runtime_type", "plugin_builds", ["runtime_type"], unique=False
    )

    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plugin_build_id", sa.Integer(), nullable=False),
        sa.Column("runtime_type", sa.String(length=16), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("environment_path", sa.String(length=1024), nullable=True),
        sa.Column("image_digest", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="INSTALLING"),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.Column("healthy_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plugin_build_id"], ["plugin_builds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plugin_build_id", name="uq_environments_plugin_build_id"),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_key", sa.String(length=80), nullable=False),
        sa.Column("plugin_build_id", sa.Integer(), nullable=False),
        sa.Column("runtime_type", sa.String(length=16), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("job_json", sa.JSON(), nullable=True),
        sa.Column("workspace_path", sa.String(length=1024), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plugin_build_id"], ["plugin_builds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_job_key", "jobs", ["job_key"], unique=True)
    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)

    op.create_table(
        "job_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("file_record_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("logical_name", sa.String(length=255), nullable=False),
        sa.Column("file_snapshot_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["file_record_id"], ["file_records.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "role", "logical_name", name="uq_job_files_role_name"),
    )

    op.create_table(
        "runner_operations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_key", sa.String(length=80), nullable=False),
        sa.Column("plugin_build_id", sa.Integer(), nullable=False),
        sa.Column("runtime_type", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        *_timestamps(),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["plugin_build_id"], ["plugin_builds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runner_operations_operation_key",
        "runner_operations",
        ["operation_key"],
        unique=True,
    )
    op.create_index(
        "ix_runner_operations_status", "runner_operations", ["status"], unique=False
    )


def downgrade() -> None:
    """Remove runtime work queues and immutable plugin metadata."""
    op.drop_index("ix_runner_operations_status", table_name="runner_operations")
    op.drop_index("ix_runner_operations_operation_key", table_name="runner_operations")
    op.drop_table("runner_operations")
    op.drop_table("job_files")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_job_key", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("environments")
    op.drop_index("ix_plugin_builds_runtime_type", table_name="plugin_builds")
    op.drop_index("ix_plugin_builds_build_key", table_name="plugin_builds")
    op.drop_table("plugin_builds")
    op.drop_table("plugin_versions")
    op.drop_index("ix_plugins_plugin_key", table_name="plugins")
    op.drop_table("plugins")
