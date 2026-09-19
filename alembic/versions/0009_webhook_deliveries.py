"""Record one row per webhook delivery attempt.

Revision ID: 0009_webhook_deliveries
Revises: 0008_jobs_replayed_from
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_webhook_deliveries"
down_revision: str | None = "0008_jobs_replayed_from"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create webhook_deliveries (immutable history of delivery attempts)."""
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("callback_id", sa.Integer(), nullable=False, index=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            index=True,
        ),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["callback_id"], ["job_callbacks.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    """Remove webhook_deliveries."""
    op.drop_table("webhook_deliveries")
