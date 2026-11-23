"""template syntax of prompt versions: the ones saved before Handlebars are legacy

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-22 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows get the server default: they keep rendering as they always did. Agent
    # versions need nothing: a stored definition without template_syntax is legacy.
    op.add_column(
        "evokai_prompts",
        sa.Column("template_syntax", sa.String(16), nullable=False, server_default="legacy"),
    )


def downgrade() -> None:
    with op.batch_alter_table("evokai_prompts") as t:
        t.drop_column("template_syntax")
