"""Create kernel event log (outbox pattern) for cross-process delivery.

Revision ID: 0010_kernel_event_log
Revises: 0009_webhook_deliveries
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_kernel_event_log"
down_revision: str | None = "0009_webhook_deliveries"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create hub_events outbox table and decouple jobs from pipeline_runs."""
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("fk_jobs_pipeline_run", type_="foreignkey")
        batch.drop_index("ix_jobs_pipeline_run_id")
    op.create_table(
        "hub_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=128), nullable=False, index=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    """Remove hub_events outbox table and restore pipeline FK."""
    with op.batch_alter_table("jobs") as batch:
        batch.create_foreign_key(
            "fk_jobs_pipeline_run", "pipeline_runs", ["pipeline_run_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_jobs_pipeline_run_id", ["pipeline_run_id"])
    op.drop_table("hub_events")
