"""traces of turns and server runs, and reviewers' notes on conversations

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-15 23:30:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evokai_traces",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("subject_key", sa.String(length=16), nullable=False),
        sa.Column("agent_name", sa.String(length=80), nullable=False),
        sa.Column("agent_version", sa.String(length=40), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("fallback", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("tool_errors", sa.Integer(), nullable=False),
        sa.Column("guard_events", sa.Integer(), nullable=False),
        sa.Column("error", sa.Boolean(), nullable=False),
        sa.Column("feedback", sa.SmallInteger(), nullable=True),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.String(length=8), nullable=False),
        sa.Column("spans", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evokai_traces_session_id", "evokai_traces", ["session_id"])
    op.create_index("ix_evokai_traces_app_time", "evokai_traces", ["application_id", "started_at"])
    op.create_index(
        "ix_evokai_traces_agent",
        "evokai_traces",
        ["application_id", "agent_name", "agent_version", "started_at"],
    )
    op.create_index("ix_evokai_traces_subject", "evokai_traces", ["application_id", "subject"])
    op.create_table(
        "evokai_review_notes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("label", sa.String(length=40), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_review_notes_session", "evokai_review_notes", ["application_id", "session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_review_notes_session", table_name="evokai_review_notes")
    op.drop_table("evokai_review_notes")
    op.drop_index("ix_evokai_traces_subject", table_name="evokai_traces")
    op.drop_index("ix_evokai_traces_agent", table_name="evokai_traces")
    op.drop_index("ix_evokai_traces_app_time", table_name="evokai_traces")
    op.drop_index("ix_evokai_traces_session_id", table_name="evokai_traces")
    op.drop_table("evokai_traces")
