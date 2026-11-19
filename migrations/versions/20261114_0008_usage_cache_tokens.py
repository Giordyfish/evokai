"""usage records count prompt cache reads and writes

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-14 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_usage_records", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("evokai_usage_records", schema=None) as batch_op:
        batch_op.drop_column("cache_write_tokens")
        batch_op.drop_column("cache_read_tokens")
