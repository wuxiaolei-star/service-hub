"""Create automation tables for callbacks, schedules, and pipelines.

Revision ID: 0005_automation
Revises: 0004_quotas_ownership
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_automation"
down_revision: str | None = "0004_quotas_ownership"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _timestamps() -> list[sa.Column[sa.DateTime]]:
    return [
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
    ]


def upgrade() -> None:
    """Create job_callbacks, schedules, pipelines, pipeline_runs."""
    op.create_table(
        "job_callbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), nullable=False, index=True),
        sa.Column("url", sa.String(length=1024), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING", index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("plugin_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("runtime_type", sa.String(length=16), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_job_id", sa.String(length=80), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "pipelines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pipeline_id", sa.Integer(), nullable=False, index=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="RUNNING", index=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["pipeline_id"], ["pipelines.id"], ondelete="CASCADE"),
    )
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("pipeline_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_pipeline_run", "pipeline_runs", ["pipeline_run_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_jobs_pipeline_run_id", ["pipeline_run_id"])


def downgrade() -> None:
    """Remove automation tables."""
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_pipeline_run_id")
        batch.drop_column("pipeline_run_id")
    op.drop_table("pipeline_runs")
    op.drop_table("pipelines")
    op.drop_table("schedules")
    op.drop_table("job_callbacks")
