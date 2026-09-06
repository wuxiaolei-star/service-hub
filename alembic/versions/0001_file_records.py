"""Create file metadata records.

Revision ID: 0001_file_records
Revises:
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_file_records"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Create the table that stores file metadata but never file payloads."""
    op.create_table(
        "file_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_key", sa.String(length=80), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("logical_name", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("extension", sa.String(length=32), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path", name="uq_file_records_relative_path"),
    )
    op.create_index("ix_file_records_file_key", "file_records", ["file_key"], unique=True)
    op.create_index("ix_file_records_sha256", "file_records", ["sha256"], unique=False)


def downgrade() -> None:
    """Remove the file metadata table."""
    op.drop_index("ix_file_records_sha256", table_name="file_records")
    op.drop_index("ix_file_records_file_key", table_name="file_records")
    op.drop_table("file_records")
