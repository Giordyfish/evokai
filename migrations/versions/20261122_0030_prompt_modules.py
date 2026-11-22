"""prompt modules: parameter schemas and data of prompts, prompt options of sessions

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-22 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evokai_prompts", sa.Column("parameters", sa.JSON(), nullable=True))
    op.add_column(
        "evokai_prompts", sa.Column("description", sa.Text(), nullable=False, server_default="")
    )
    op.add_column("evokai_prompts", sa.Column("data", sa.JSON(), nullable=True))
    op.add_column("evokai_sessions", sa.Column("prompt_options", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("evokai_sessions", "prompt_options")
    op.drop_column("evokai_prompts", "data")
    op.drop_column("evokai_prompts", "description")
    op.drop_column("evokai_prompts", "parameters")
