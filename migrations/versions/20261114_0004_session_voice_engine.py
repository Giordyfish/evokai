"""session voice engine (browser or server)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-14 17:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_sessions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "voice_engine", sa.String(length=16), server_default="browser", nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("evokai_sessions", schema=None) as batch_op:
        batch_op.drop_column("voice_engine")
