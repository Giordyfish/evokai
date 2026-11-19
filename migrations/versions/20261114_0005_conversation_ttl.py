"""conversation time to live per application

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-14 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("conversation_ttl_days", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.drop_column("conversation_ttl_days")
