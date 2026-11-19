"""data protection: an application's profile (residency, jurisdiction, retention)

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-19 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("evokai_applications", sa.Column("data_protection", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("evokai_applications", "data_protection")
