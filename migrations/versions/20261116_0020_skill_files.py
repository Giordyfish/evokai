"""skill files: references, data and scripts beside SKILL.md; descriptions up to 1,024 characters

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_skills", schema=None) as batch_op:
        batch_op.alter_column(
            "description", existing_type=sa.String(length=1000), type_=sa.String(length=1024)
        )
    op.create_table(
        "evokai_skill_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("skill_name", sa.String(length=80), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "skill_name", "path", name="uq_evokai_skill_file"),
    )


def downgrade() -> None:
    op.drop_table("evokai_skill_files")
    with op.batch_alter_table("evokai_skills", schema=None) as batch_op:
        batch_op.alter_column(
            "description", existing_type=sa.String(length=1024), type_=sa.String(length=1000)
        )
