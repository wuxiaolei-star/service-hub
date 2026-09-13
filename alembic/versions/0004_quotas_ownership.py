"""Add quota overrides and file ownership columns.

Revision ID: 0004_quotas_ownership
Revises: 0003_auth_rbac_audit
Create Date: 2026-09-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_quotas_ownership"
down_revision: str | None = "0003_auth_rbac_audit"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add per-user quota overrides and file ownership."""
    op.add_column(
        "users",
        sa.Column("quota_total_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("quota_file_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("quota_concurrent_jobs", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("file_records") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_file_records_owner_user", "users", ["owner_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_file_records_owner_user_id", ["owner_user_id"])

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_owner_user", "users", ["owner_user_id"], ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_jobs_owner_user_id", ["owner_user_id"])


def downgrade() -> None:
    """Remove quota overrides and file ownership."""
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_owner_user_id")
        batch.drop_column("owner_user_id")

    with op.batch_alter_table("file_records") as batch:
        batch.drop_index("ix_file_records_owner_user_id")
        batch.drop_column("owner_user_id")
    op.drop_column("users", "quota_concurrent_jobs")
    op.drop_column("users", "quota_file_count")
    op.drop_column("users", "quota_total_bytes")
