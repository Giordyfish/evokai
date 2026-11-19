"""ask rules: the application's rules that make tool calls wait for the person's confirmation

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evokai_applications", sa.Column("ask_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("evokai_applications", "ask_rules")
