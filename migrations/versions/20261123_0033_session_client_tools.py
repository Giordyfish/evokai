"""custom client tools declared by a session token, kept with the conversation

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-23 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evokai_sessions", sa.Column("client_tools", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_sessions") as t:
        t.drop_column("client_tools")
