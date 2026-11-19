"""conversation summaries: context compaction of conversations stored by Evokai

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_conversation_summaries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("up_to_position", sa.Integer(), nullable=False),
        sa.Column("up_to_message_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("compactions", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )


def downgrade() -> None:
    op.drop_table("evokai_conversation_summaries")
