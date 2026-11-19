"""console single sign-on, console users and settings, the audit log

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-15 18:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.add_column(sa.Column("audit_ttl_days", sa.Integer(), nullable=True))
    op.create_table(
        "evokai_audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=True),
        sa.Column("actor", sa.String(length=200), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("target", sa.String(length=300), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evokai_audit_app_time", "evokai_audit_log", ["application_id", "at"])
    op.create_table(
        "evokai_console_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("issuer", sa.String(length=300), nullable=False),
        sa.Column("sub", sa.String(length=300), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "sub", name="uq_evokai_console_user"),
    )
    op.create_table(
        "evokai_console_settings",
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("evokai_console_settings")
    op.drop_table("evokai_console_users")
    op.drop_index("ix_evokai_audit_app_time", table_name="evokai_audit_log")
    op.drop_table("evokai_audit_log")
    with op.batch_alter_table("evokai_applications", schema=None) as batch_op:
        batch_op.drop_column("audit_ttl_days")
