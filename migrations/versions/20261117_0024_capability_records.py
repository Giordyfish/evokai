"""capability records: what harness capabilities keep (memory files, plans, steps, spill, spend)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-17 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_capability_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("capability", sa.String(length=40), nullable=False),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=300), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("version", sa.String(length=36), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            "subject",
            "capability",
            "scope",
            "key",
            name="uq_evokai_capability_record",
        ),
    )
    op.create_index(
        "ix_evokai_capability_records_session",
        "evokai_capability_records",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_evokai_capability_records_expires",
        "evokai_capability_records",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_capability_records_expires", table_name="evokai_capability_records")
    op.drop_index("ix_evokai_capability_records_session", table_name="evokai_capability_records")
    op.drop_table("evokai_capability_records")
