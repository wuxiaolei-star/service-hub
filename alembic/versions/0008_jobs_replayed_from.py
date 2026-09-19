"""Track rerun provenance on Jobs.

Revision ID: 0008_jobs_replayed_from
Revises: 0007_login_attempts
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_jobs_replayed_from"
down_revision: str | None = "0007_login_attempts"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Add jobs.replayed_from (no FK: the source Job may be purged by retention)."""
    op.add_column("jobs", sa.Column("replayed_from", sa.String(length=80), nullable=True))


def downgrade() -> None:
    """Remove jobs.replayed_from."""
    op.drop_column("jobs", "replayed_from")
