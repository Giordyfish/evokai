"""longer skill descriptions

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14 18:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_skills", schema=None) as batch_op:
        batch_op.alter_column(
            "description", existing_type=sa.String(length=300), type_=sa.String(length=1000)
        )


def downgrade() -> None:
    with op.batch_alter_table("evokai_skills", schema=None) as batch_op:
        batch_op.alter_column(
            "description", existing_type=sa.String(length=1000), type_=sa.String(length=300)
        )
