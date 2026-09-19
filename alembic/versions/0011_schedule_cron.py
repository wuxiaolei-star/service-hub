"""Add cron scheduling columns to schedules.

Revision ID: 0011_schedule_cron
Revises: 0010_kernel_event_log
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_schedule_cron"
down_revision: str | None = "0010_kernel_event_log"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add cron_expr and missed_run_policy to schedules."""
    with op.batch_alter_table("schedules") as batch:
        batch.add_column(sa.Column("cron_expr", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column(
                "missed_run_policy",
                sa.String(length=16),
                nullable=False,
                server_default="skip",
            )
        )


def downgrade() -> None:
    """Remove cron scheduling columns from schedules."""
    with op.batch_alter_table("schedules") as batch:
        batch.drop_column("missed_run_policy")
        batch.drop_column("cron_expr")
