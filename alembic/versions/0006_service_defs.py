"""Create service definitions for long-running containers.

Revision ID: 0006_service_defs
Revises: 0005_automation
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_service_defs"
down_revision: str | None = "0005_automation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create service_defs."""
    op.create_table(
        "service_defs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=63), nullable=False, unique=True, index=True),
        sa.Column("image", sa.String(length=255), nullable=False),
        sa.Column(
            "container_name", sa.String(length=80), nullable=False, unique=True, index=True
        ),
        sa.Column("ports_json", sa.JSON(), nullable=False),
        sa.Column("env_json", sa.JSON(), nullable=False),
        sa.Column("mounts_json", sa.JSON(), nullable=False),
        sa.Column("command_json", sa.JSON(), nullable=True),
        sa.Column("user_label", sa.String(length=64), nullable=False, server_default="65532:65532"),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="RUNNING"),
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


def downgrade() -> None:
    """Remove service_defs."""
    op.drop_table("service_defs")
