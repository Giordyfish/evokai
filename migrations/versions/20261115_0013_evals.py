"""evals: test cases per agent, runs that compare two versions, and their results

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15 20:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "evokai_eval_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("page", sa.JSON(), nullable=True),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "agent_name", "name", name="uq_evokai_eval_case"),
    )
    op.create_table(
        "evokai_eval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("agent_name", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("baseline", sa.String(length=40), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=80), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("ended_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_eval_runs_agent",
        "evokai_eval_runs",
        ["application_id", "agent_name", "started_at"],
    )
    op.create_table(
        "evokai_eval_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("case_name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("tools", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evokai_eval_results_run_id", "evokai_eval_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_evokai_eval_results_run_id", table_name="evokai_eval_results")
    op.drop_table("evokai_eval_results")
    op.drop_index("ix_evokai_eval_runs_agent", table_name="evokai_eval_runs")
    op.drop_table("evokai_eval_runs")
    op.drop_table("evokai_eval_cases")
