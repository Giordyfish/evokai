"""long-term memory with consent, red team runs and the publish gate, cost alerts

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16 12:00:00

- ``evokai_memories``: kinds (preference, semantic, episodic), the agent and conversation it came
  from, its embedding, use counts, supersession and an expiry; facts up to 1,000 characters.
- ``evokai_memory_consents``: a subject's answer to ``memory_mode: ask``.
- ``evokai_agents.publish_state``: a version waiting for its red team run, or blocked by the gate.
- ``evokai_eval_runs.suite`` and ``evokai_eval_results.variant``: red team and model comparison
  runs next to the eval cases.
- ``evokai_cost_alerts``: forecast thresholds crossed, once per scope, month and threshold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("evokai_memories", schema=None) as b:
        b.alter_column("fact", existing_type=sa.String(length=300), type_=sa.String(length=1000))
        b.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="preference")
        )
        b.add_column(
            sa.Column("agent_name", sa.String(length=80), nullable=False, server_default="")
        )
        b.add_column(sa.Column("session_id", sa.String(length=36), nullable=True))
        b.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="agent")
        )
        b.add_column(sa.Column("embedding", sa.LargeBinary(), nullable=True))
        b.add_column(sa.Column("embedding_model", sa.String(length=120), nullable=True))
        b.add_column(sa.Column("updated_at", TS, nullable=True))
        b.add_column(sa.Column("last_used_at", TS, nullable=True))
        b.add_column(sa.Column("uses", sa.Integer(), nullable=False, server_default="0"))
        b.add_column(sa.Column("superseded_by", sa.String(length=36), nullable=True))
        b.add_column(sa.Column("expires_at", TS, nullable=True))
    op.create_table(
        "evokai_memory_consents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("decided_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "subject", name="uq_evokai_memory_consent"),
    )
    with op.batch_alter_table("evokai_agents", schema=None) as b:
        b.add_column(sa.Column("publish_state", sa.String(length=16), nullable=True))
    with op.batch_alter_table("evokai_eval_runs", schema=None) as b:
        b.add_column(
            sa.Column("suite", sa.String(length=16), nullable=False, server_default="cases")
        )
    with op.batch_alter_table("evokai_eval_results", schema=None) as b:
        b.add_column(sa.Column("variant", sa.String(length=120), nullable=True))
    op.create_table(
        "evokai_cost_alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("forecast_pct", sa.Float(), nullable=False),
        sa.Column("used_pct", sa.Float(), nullable=False),
        sa.Column("fired_at", TS, nullable=False),
        sa.Column("delivery", sa.JSON(), nullable=False),
        sa.Column("acknowledged_at", TS, nullable=True),
        sa.Column("acknowledged_by", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "scope", "period", "threshold", name="uq_evokai_cost_alert"
        ),
    )


def downgrade() -> None:
    op.drop_table("evokai_cost_alerts")
    with op.batch_alter_table("evokai_eval_results", schema=None) as b:
        b.drop_column("variant")
    with op.batch_alter_table("evokai_eval_runs", schema=None) as b:
        b.drop_column("suite")
    with op.batch_alter_table("evokai_agents", schema=None) as b:
        b.drop_column("publish_state")
    op.drop_table("evokai_memory_consents")
    with op.batch_alter_table("evokai_memories", schema=None) as b:
        for column in (
            "expires_at",
            "superseded_by",
            "uses",
            "last_used_at",
            "updated_at",
            "embedding_model",
            "embedding",
            "source",
            "session_id",
            "agent_name",
            "kind",
        ):
            b.drop_column(column)
        b.alter_column("fact", existing_type=sa.String(length=1000), type_=sa.String(length=300))
