"""console preferences: language, appearance and accessibility per console identity

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_console_preferences",
        sa.Column("identity", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("identity"),
    )


def downgrade() -> None:
    op.drop_table("evokai_console_preferences")
