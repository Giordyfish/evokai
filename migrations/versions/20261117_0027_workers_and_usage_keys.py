"""MCP servers marked as Evokai capability workers; usage records with an idempotency key

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-18 09:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("worker", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    with op.batch_alter_table("evokai_usage_records", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dedup_key", sa.String(length=200), nullable=True))
        batch_op.create_index("ux_evokai_usage_dedup", ["dedup_key"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("evokai_usage_records", schema=None) as batch_op:
        batch_op.drop_index("ux_evokai_usage_dedup")
        batch_op.drop_column("dedup_key")
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.drop_column("worker")
