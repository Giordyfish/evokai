"""mcp servers may be ready connectors served by Evokai (kind and configuration)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.add_column(sa.Column("connector", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("config", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_mcp_servers", schema=None) as batch_op:
        batch_op.drop_column("config")
        batch_op.drop_column("connector")
