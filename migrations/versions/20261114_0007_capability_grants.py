"""capability grants, per-conversation storage mode, agent authors, MCP server status

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "kind", "name", name="uq_evokai_grant"),
    )
    op.create_index(
        op.f("ix_evokai_grants_application_id"), "evokai_grants", ["application_id"], unique=False
    )
    with op.batch_alter_table("evokai_sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_mode", sa.String(length=16), nullable=True))
    with op.batch_alter_table("evokai_agents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by", sa.String(length=80), nullable=True))
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("tools", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("status", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("latency_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("last_error", sa.String(length=300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.drop_column("last_error")
        batch_op.drop_column("latency_ms")
        batch_op.drop_column("status")
        batch_op.drop_column("checked_at")
        batch_op.drop_column("tools")
    with op.batch_alter_table("evokai_agents", schema=None) as batch_op:
        batch_op.drop_column("created_by")
    with op.batch_alter_table("evokai_sessions", schema=None) as batch_op:
        batch_op.drop_column("storage_mode")
    op.drop_index(op.f("ix_evokai_grants_application_id"), table_name="evokai_grants")
    op.drop_table("evokai_grants")
