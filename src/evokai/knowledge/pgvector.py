"""Postgres DDL of the pgvector index: what migration 0016 (and tests) add to the passages table.

``embedding_v`` is a ``vector`` without a fixed size (bases choose 256, 512 or 1024 dimensions);
an HNSW index per size covers the passages of that size (a partial index on ``dims``, the vector
cast to its size), with cosine distance. ``tsv`` is the passage's text search form, with a GIN
index. The extension's schema is named in full, so the runtime role finds the type and operators
whatever its ``search_path``.
"""

from __future__ import annotations

SIZES = (256, 512, 1024)


def statements(ns: str) -> list[str]:
    """The statements that add the vector and text search columns and their indexes.

    Args:
        ns: The schema of the ``vector`` extension.

    Returns:
        SQL statements (idempotent).
    """
    q = f'"{ns}"'
    out = [
        f"ALTER TABLE evokai_knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_v {q}.vector",
        "ALTER TABLE evokai_knowledge_chunks ADD COLUMN IF NOT EXISTS tsv tsvector",
        "CREATE INDEX IF NOT EXISTS ix_evokai_knowledge_chunks_tsv "
        "ON evokai_knowledge_chunks USING gin (tsv)",
    ]
    out += [
        f"CREATE INDEX IF NOT EXISTS ix_evokai_knowledge_chunks_hnsw_{n} "
        f"ON evokai_knowledge_chunks USING hnsw ((embedding_v::{q}.vector({n})) "
        f"{q}.vector_cosine_ops) WHERE dims = {n}"
        for n in SIZES
    ]
    return out


DROP = [
    *(f"DROP INDEX IF EXISTS ix_evokai_knowledge_chunks_hnsw_{n}" for n in SIZES),
    "DROP INDEX IF EXISTS ix_evokai_knowledge_chunks_tsv",
    "ALTER TABLE evokai_knowledge_chunks DROP COLUMN IF EXISTS tsv",
    "ALTER TABLE evokai_knowledge_chunks DROP COLUMN IF EXISTS embedding_v",
]
"""Undo ``statements``."""
