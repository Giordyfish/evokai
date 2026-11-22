"""system applications: shipped with Evokai (the built-in assistant), locked until unlocked

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-22 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications") as t:
        t.add_column(sa.Column("system", sa.Boolean(), nullable=False, server_default=sa.false()))
        t.add_column(sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()))
        t.add_column(sa.Column("system_version", sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_applications") as t:
        t.drop_column("system_version")
        t.drop_column("locked")
        t.drop_column("system")
