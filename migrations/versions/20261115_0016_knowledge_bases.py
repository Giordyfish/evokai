"""knowledge bases: bases, documents, passages and files; pgvector columns on Postgres

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-15 23:30:00

On Postgres the ``vector`` extension is used when it is installed (``evokai db-setup`` installs it
as the owner) or can be installed now; the passages then get ``embedding_v`` and ``tsv`` with their
indexes (``evokai.knowledge.pgvector``). Without it the portable search is used.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import DBAPIError

from evokai.knowledge import pgvector

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
log = logging.getLogger("alembic.runtime.migration")


def _vector_schema(bind: sa.Connection) -> str | None:
    q = sa.text(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'vector'"
    )
    row = bind.execute(q).first()
    if row:
        return str(row[0])
    try:
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except DBAPIError:
        log.warning("pgvector is not available: knowledge search uses the portable index")
        return None
    row = bind.execute(q).first()
    return str(row[0]) if row else None


def upgrade() -> None:
    op.create_table(
        "evokai_knowledge_bases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("chunking", sa.JSON(), nullable=False),
        sa.Column("retrieval", sa.JSON(), nullable=False),
        sa.Column("access", sa.JSON(), nullable=False),
        sa.Column("link_template", sa.String(length=500), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id", "name", name="uq_evokai_knowledge_base"),
    )
    op.create_table(
        "evokai_knowledge_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("kb_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("mime", sa.String(length=120), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("access", sa.JSON(), nullable=False),
        sa.Column("chunks", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("modified", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("ingested_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "source", name="uq_evokai_knowledge_document"),
    )
    op.create_index("ix_evokai_knowledge_documents_kb_id", "evokai_knowledge_documents", ["kb_id"])
    op.create_index(
        "ix_evokai_knowledge_documents_checksum",
        "evokai_knowledge_documents",
        ["kb_id", "checksum"],
    )
    op.create_table(
        "evokai_knowledge_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("kb_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(length=500), nullable=False),
        sa.Column("headings", sa.JSON(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evokai_knowledge_chunks_kb_id", "evokai_knowledge_chunks", ["kb_id"])
    op.create_index(
        "ix_evokai_knowledge_chunks_document_id", "evokai_knowledge_chunks", ["document_id"]
    )
    op.create_table(
        "evokai_knowledge_files",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("application_id", sa.String(length=36), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("document_id"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and (ns := _vector_schema(bind)):
        for statement in pgvector.statements(ns):
            op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for statement in pgvector.DROP:
            op.execute(statement)
    op.drop_table("evokai_knowledge_files")
    op.drop_index("ix_evokai_knowledge_chunks_document_id", table_name="evokai_knowledge_chunks")
    op.drop_index("ix_evokai_knowledge_chunks_kb_id", table_name="evokai_knowledge_chunks")
    op.drop_table("evokai_knowledge_chunks")
    op.drop_index("ix_evokai_knowledge_documents_checksum", table_name="evokai_knowledge_documents")
    op.drop_index("ix_evokai_knowledge_documents_kb_id", table_name="evokai_knowledge_documents")
    op.drop_table("evokai_knowledge_documents")
    op.drop_table("evokai_knowledge_bases")
