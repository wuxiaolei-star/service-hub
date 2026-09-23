"""Add hot-path indexes for job claiming and due-run/due-callback scans.

Revision ID: 0012_hot_path_indexes
Revises: 0011_schedule_cron
Create Date: 2026-09-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_hot_path_indexes"
down_revision: str | None = "0011_schedule_cron"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Index the runner claim query and the scheduler due scans."""
    op.create_index(
        "ix_jobs_runtime_status_created",
        "jobs",
        ["runtime_type", "status", "created_at"],
    )
    op.create_index("ix_schedules_next_run_at", "schedules", ["next_run_at"])
    op.create_index("ix_job_callbacks_next_attempt_at", "job_callbacks", ["next_attempt_at"])


def downgrade() -> None:
    """Remove the hot-path indexes."""
    op.drop_index("ix_job_callbacks_next_attempt_at", table_name="job_callbacks")
    op.drop_index("ix_schedules_next_run_at", table_name="schedules")
    op.drop_index("ix_jobs_runtime_status_created", table_name="jobs")
