"""voice settings: how the engine speaks with a catalogue voice (model, speed, engine options)

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-26 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_voices") as t:
        t.add_column(sa.Column("settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_voices") as t:
        t.drop_column("settings")
