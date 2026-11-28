"""gateway proxy: people's e-mail, the request's surface, the identities the proxy learns

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-28 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("evokai_api_keys") as batch:
        batch.add_column(sa.Column("email", sa.String(length=254), nullable=True))
    op.create_index("ix_evokai_api_keys_email", "evokai_api_keys", ["email"])
    with op.batch_alter_table("evokai_gateway_requests") as batch:
        batch.add_column(sa.Column("surface", sa.String(length=24), nullable=True))
    op.create_table(
        "evokai_gateway_proxy_identities",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("account_id", sa.String(length=80), nullable=True),
        sa.Column("org_id", sa.String(length=80), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key_hash"),
    )
    op.create_index(
        "ix_evokai_gateway_proxy_identities_expiry",
        "evokai_gateway_proxy_identities",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_evokai_gateway_proxy_identities_expiry", table_name="evokai_gateway_proxy_identities"
    )
    op.drop_table("evokai_gateway_proxy_identities")
    with op.batch_alter_table("evokai_gateway_requests") as batch:
        batch.drop_column("surface")
    op.drop_index("ix_evokai_api_keys_email", table_name="evokai_api_keys")
    with op.batch_alter_table("evokai_api_keys") as batch:
        batch.drop_column("email")
