"""capability credentials: the keys of web search, integrations and sandboxes per application

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_capability_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("values_encrypted", sa.Text(), nullable=False),
        sa.Column("names", sa.JSON(), nullable=False),
        sa.Column("plain", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "provider", name="uq_evokai_capability_credential"),
    )


def downgrade() -> None:
    op.drop_table("evokai_capability_credentials")
