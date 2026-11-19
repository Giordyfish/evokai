"""privacy: data residency per application and subject session revocations

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-15 14:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("residency", sa.JSON(), nullable=True))
    op.create_table(
        "evokai_subject_revocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("subject_key", sa.String(length=64), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_subject_revocations_key",
        "evokai_subject_revocations",
        ["application_id", "subject_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_subject_revocations_key", table_name="evokai_subject_revocations")
    op.drop_table("evokai_subject_revocations")
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.drop_column("residency")
