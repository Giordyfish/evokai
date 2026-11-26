"""policy versions and policy tests; every application starts at version 1 of its current policy

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-26 12:00:00
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    versions = op.create_table(
        "evokai_policy_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "number", name="uq_evokai_policy_version"),
    )
    op.create_table(
        "evokai_policy_tests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=True),
        sa.Column("controls", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "name", name="uq_evokai_policy_test"),
    )
    # Every existing application gets version 1: the policy it has now.
    apps = sa.table(
        "evokai_applications",
        sa.column("id", sa.String(36)),
        sa.column("security", sa.JSON()),
        sa.column("data_protection", sa.JSON()),
    )
    rows = op.get_bind().execute(sa.select(apps.c.id, apps.c.security, apps.c.data_protection))
    now = datetime.now(UTC)
    values = [
        {
            "id": str(uuid.uuid4()),
            "owner_id": app_id,
            "number": 1,
            "content": {"security": security or None, "data_protection": dp or None},
            "created_at": now,
            "created_by": "migration",
            "note": "The policy before versions",
        }
        for app_id, security, dp in rows
    ]
    if values:
        op.bulk_insert(versions, values)


def downgrade() -> None:
    op.drop_table("evokai_policy_tests")
    op.drop_table("evokai_policy_versions")
