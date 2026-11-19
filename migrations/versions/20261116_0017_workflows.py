"""workflows: versioned definitions, runs, step states (leases, retries) and approvals

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-16 01:00:00

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "evokai_workflow_approvals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=48), nullable=False),
        sa.Column("workflow_name", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("question", sa.String(length=1000), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("approvers", sa.JSON(), nullable=False),
        sa.Column("on_timeout", sa.String(length=8), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("decided_by", sa.String(length=200), nullable=True),
        sa.Column("decided_via", sa.String(length=16), nullable=True),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column("decided_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_workflow_approvals_queue",
        "evokai_workflow_approvals",
        ["application_id", "status", "created_at"],
    )
    op.create_index("ix_evokai_workflow_approvals_run_id", "evokai_workflow_approvals", ["run_id"])

    op.create_table(
        "evokai_workflow_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("workflow_name", sa.String(length=80), nullable=False),
        sa.Column("workflow_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=60), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("trigger_key", sa.String(length=200), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("masked", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "workflow_name", "trigger_key", name="uq_evokai_workflow_run_trigger"
        ),
    )
    op.create_index(
        "ix_evokai_workflow_runs_app_time", "evokai_workflow_runs", ["application_id", "started_at"]
    )
    op.create_index(
        "ix_evokai_workflow_runs_subject", "evokai_workflow_runs", ["application_id", "subject"]
    )

    op.create_table(
        "evokai_workflow_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=48), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("due_at", TS, nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_until", TS, nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("input", sa.JSON(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("cost_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("started_at", TS, nullable=True),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "step_id", name="uq_evokai_workflow_step"),
    )
    op.create_index("ix_evokai_workflow_steps_due", "evokai_workflow_steps", ["status", "due_at"])
    op.create_index("ix_evokai_workflow_steps_run_id", "evokai_workflow_steps", ["run_id"])

    op.create_table(
        "evokai_workflows",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "name", "version", name="uq_evokai_workflow_version"),
    )


def downgrade() -> None:
    op.drop_table("evokai_workflows")
    op.drop_index("ix_evokai_workflow_steps_run_id", table_name="evokai_workflow_steps")
    op.drop_index("ix_evokai_workflow_steps_due", table_name="evokai_workflow_steps")

    op.drop_table("evokai_workflow_steps")
    op.drop_index("ix_evokai_workflow_runs_subject", table_name="evokai_workflow_runs")
    op.drop_index("ix_evokai_workflow_runs_app_time", table_name="evokai_workflow_runs")

    op.drop_table("evokai_workflow_runs")
    op.drop_index("ix_evokai_workflow_approvals_run_id", table_name="evokai_workflow_approvals")
    op.drop_index("ix_evokai_workflow_approvals_queue", table_name="evokai_workflow_approvals")

    op.drop_table("evokai_workflow_approvals")
