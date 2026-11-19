"""caps per calendar window and in the application's currency; alert periods name a window

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-16 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False)
        )
        batch_op.add_column(sa.Column("exchange_rate", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("caps", sa.JSON(), nullable=True))
    with op.batch_alter_table("evokai_cost_alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(length=7), type_=sa.String(length=16)
        )


def downgrade() -> None:
    with op.batch_alter_table("evokai_cost_alerts", schema=None) as batch_op:
        batch_op.alter_column(
            "period", existing_type=sa.String(length=16), type_=sa.String(length=7)
        )
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.drop_column("caps")
        batch_op.drop_column("exchange_rate")
        batch_op.drop_column("currency")
