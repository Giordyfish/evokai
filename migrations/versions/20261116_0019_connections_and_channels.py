"""connections and channels: per-user OAuth connections, e-mail, Teams and Slack channels

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-16 12:00:00

Follows 0018 (memory, red team and cost), written on another branch at the same time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "evokai_oauth_connections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("account", sa.String(length=320), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("expires_at", TS, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id", "subject", "provider", name="uq_evokai_oauth_connection"
        ),
    )
    op.create_index(
        "ix_evokai_oauth_connections_provider",
        "evokai_oauth_connections",
        ["application_id", "provider"],
    )
    op.create_table(
        "evokai_channels",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("agent", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("last_event_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "kind", "name", name="uq_evokai_channel_name"),
    )
    op.create_table(
        "evokai_channel_threads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=36), nullable=False),
        sa.Column("thread_key", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("reference", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id", "thread_key", name="uq_evokai_channel_thread"),
    )
    op.create_index(
        "ix_evokai_channel_threads_subject",
        "evokai_channel_threads",
        ["application_id", "subject"],
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_channel_threads_subject", table_name="evokai_channel_threads")
    op.drop_table("evokai_channel_threads")
    op.drop_table("evokai_channels")
    op.drop_index("ix_evokai_oauth_connections_provider", table_name="evokai_oauth_connections")
    op.drop_table("evokai_oauth_connections")
