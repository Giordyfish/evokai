"""gateways: applications.kind and gateway settings, and the gateway's request records

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-26 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="agents")
        )
        batch.add_column(sa.Column("gateway", sa.JSON(), nullable=True))
    op.create_table(
        "evokai_gateway_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("key_id", sa.String(length=36), nullable=True),
        sa.Column("person", sa.String(length=120), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("control", sa.String(length=40), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("request_bytes", sa.Integer(), nullable=False),
        sa.Column("response_bytes", sa.Integer(), nullable=False),
        sa.Column("first_byte_ms", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evokai_gateway_requests_time",
        "evokai_gateway_requests",
        ["application_id", "created_at"],
    )
    op.create_index(
        "ix_evokai_gateway_requests_key", "evokai_gateway_requests", ["key_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_evokai_gateway_requests_key", table_name="evokai_gateway_requests")
    op.drop_index("ix_evokai_gateway_requests_time", table_name="evokai_gateway_requests")
    op.drop_table("evokai_gateway_requests")
    with op.batch_alter_table("evokai_applications") as batch:
        batch.drop_column("gateway")
        batch.drop_column("kind")
