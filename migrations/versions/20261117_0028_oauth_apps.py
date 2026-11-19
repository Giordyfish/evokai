"""oauth apps: the application's OAuth client at GitHub, Linear, Notion and Slack

Revision ID: 0028
Revises: 0023
Create Date: 2026-11-17 21:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_oauth_apps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("client_id", sa.String(length=300), nullable=False),
        sa.Column("client_secret_encrypted", sa.Text(), nullable=False),
        sa.Column("writes", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "provider", name="uq_evokai_oauth_app"),
    )


def downgrade() -> None:
    op.drop_table("evokai_oauth_apps")
