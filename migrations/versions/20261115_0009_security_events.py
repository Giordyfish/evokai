"""security layer: the application's policy and the security events

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-15 10:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("security", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("security_event_ttl_days", sa.Integer(), nullable=True))
    op.create_table(
        "evokai_security_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("control", sa.String(length=40), nullable=False),
        sa.Column("rule", sa.String(length=60), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=True),
        sa.Column("agent_name", sa.String(length=80), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("tool", sa.String(length=120), nullable=True),
        sa.Column("snippet", sa.String(length=400), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_security_events_app_time",
        "evokai_security_events",
        ["application_id", "created_at"],
    )
    op.create_index(
        "ix_evokai_security_events_subject", "evokai_security_events", ["application_id", "subject"]
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_security_events_subject", table_name="evokai_security_events")
    op.drop_index("ix_evokai_security_events_app_time", table_name="evokai_security_events")
    op.drop_table("evokai_security_events")
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.drop_column("security_event_ttl_days")
        batch_op.drop_column("security")
