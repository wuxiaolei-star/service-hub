"""Create the login failure counter used by the credential throttle.

Revision ID: 0007_login_attempts
Revises: 0006_service_defs
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_login_attempts"
down_revision: str | None = "0006_service_defs"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create login_attempts."""
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, index=True),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_failure_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("username", "ip", name="uq_login_attempts_identity"),
    )


def downgrade() -> None:
    """Remove login_attempts."""
    op.drop_table("login_attempts")
