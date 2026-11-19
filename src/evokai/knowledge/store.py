"""Where knowledge lives: bases, documents, files and passages, and the two search indexes.

One interface, two implementations chosen at run time:

- **pgvector** (Postgres with the ``vector`` extension and the columns the migration adds):
  passages carry ``embedding_v`` (a ``vector``, with partial HNSW indexes for 256, 512 and 1024
  dimensions, cosine distance) and ``tsv`` (a ``tsvector`` in the base's language, GIN index);
  both searches run in the database.
- **portable** (SQLite in tests, any other database, Postgres without pgvector): passages carry
  their embedding as float32 bytes; similarity is computed in Python by brute force (cosine) and
  the text search is BM25 over the passages' words. Right for tests and small bases.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.engine import Database
from evokai.knowledge.chunking import Chunk
from evokai.knowledge.embeddings import cosine, pack, unpack

log = logging.getLogger("evokai.knowledge")

LANGUAGES = (
    "simple", "italian", "english", "french", "german", "spanish", "portuguese", "dutch",
)  # fmt: skip
"""Text search configurations a base may choose (Postgres names)."""


@dataclass
class Hit:
    """A passage found by one of the searches."""

    chunk_id: str
    score: float


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return [w[:-1] if len(w) > 4 and w[-1] in "aeios" else w for w in re.findall(r"\w{2,}", text)]


def bm25(query: str, docs: list[tuple[str, str]], limit: int) -> list[Hit]:
    """Okapi BM25 (k1 1.2, b 0.75) over passages, by brute force.

    Args:
        query: The question.
        docs: (chunk id, text) pairs.
        limit: How many to return.

    Returns:
        The passages that share a word with the question, best first.
    """
    terms = set(_words(query))
    if not terms or not docs:
        return []
    tokenized = [(cid, Counter(_words(text))) for cid, text in docs]
    n = len(tokenized)
    avg = sum(sum(c.values()) for _, c in tokenized) / n or 1.0
    df = {t: sum(1 for _, c in tokenized if t in c) for t in terms}
    hits = []
    for cid, counts in tokenized:
        length = sum(counts.values())
        score = 0.0
        for t in terms:
            f = counts.get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * 2.2 / (f + 1.2 * (1 - 0.75 + 0.75 * length / avg))
        if score > 0:
            hits.append(Hit(cid, score))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


class Index:
    """The portable index: embeddings as bytes, cosine and BM25 in Python."""

    kind = "portable"

    def __init__(self, db: Database) -> None:
        """Bind to the database.

        Args:
            db: The database.
        """
        self.db = db

    async def write(
        self,
        s: AsyncSession,
        rows: list[m.KnowledgeChunk],
        vectors: list[list[float]],
        language: str,
    ) -> None:
        """Store new passages with their embeddings (in the caller's transaction)."""
        del language  # BM25 here needs no per-language form
        for row, v in zip(rows, vectors, strict=True):
            row.embedding = pack(v)
            s.add(row)

    def _scope(self, kb_ids: Sequence[str], doc_ids: Sequence[str] | None) -> Any:
        c = m.KnowledgeChunk
        where = [c.kb_id.in_(list(kb_ids))]
        if doc_ids is not None:
            where.append(c.document_id.in_(list(doc_ids)))
        return sa.and_(*where)

    async def vector_search(
        self,
        kb_ids: Sequence[str],
        dims: int,
        query: Sequence[float],
        doc_ids: Sequence[str] | None,
        limit: int,
    ) -> list[Hit]:
        """The passages closest to a query vector.

        Returns:
            Hits with their cosine similarity, best first.
        """
        c = m.KnowledgeChunk
        async with self.db.session() as s:
            q = sa.select(c.id, c.embedding).where(self._scope(kb_ids, doc_ids), c.dims == dims)
            rows = (await s.execute(q)).all()
        hits = [Hit(cid, cosine(query, unpack(e))) for cid, e in rows if e]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    async def text_search(
        self,
        kb_ids: Sequence[str],
        query: str,
        language: str,
        doc_ids: Sequence[str] | None,
        limit: int,
    ) -> list[Hit]:
        """The passages that match the query's words.

        Returns:
            Hits with their BM25 score, best first.
        """
        del language
        c = m.KnowledgeChunk
        async with self.db.session() as s:
            q = sa.select(c.id, c.text, c.section).where(self._scope(kb_ids, doc_ids))
            rows = (await s.execute(q)).all()
        return bm25(query, [(cid, f"{section}\n{text}") for cid, text, section in rows], limit)

    async def vectors(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]:
        """The embeddings of some passages (for diversity).

        Returns:
            Chunk id to vector.
        """
        c = m.KnowledgeChunk
        async with self.db.session() as s:
            q = sa.select(c.id, c.embedding).where(c.id.in_(list(chunk_ids)))
            return {cid: unpack(e) for cid, e in (await s.execute(q)).all() if e}


def vector_literal(v: Sequence[float]) -> str:
    """A vector in pgvector's text form.

    Returns:
        ``[0.1,0.2,...]``.
    """
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


class PgVectorIndex(Index):
    """Postgres with pgvector: HNSW over ``embedding_v`` and a GIN index over ``tsv``."""

    kind = "pgvector"

    def __init__(self, db: Database, ns: str) -> None:
        """Bind to the database.

        Args:
            db: The database.
            ns: The schema the ``vector`` extension lives in.
        """
        super().__init__(db)
        self.ns = f'"{ns}"'

    def _filter(self, doc_ids: Sequence[str] | None) -> tuple[str, dict[str, Any]]:
        if doc_ids is None:
            return "", {}
        return " AND document_id = ANY(:docs)", {"docs": list(doc_ids)}

    async def write(
        self,
        s: AsyncSession,
        rows: list[m.KnowledgeChunk],
        vectors: list[list[float]],
        language: str,
    ) -> None:
        """Store new passages with their vectors and text search forms."""
        for row in rows:
            s.add(row)
        await s.flush()
        # Only trusted parts are formatted in: the schema name, integer sizes, fixed filters.
        stmt = sa.text(
            f"UPDATE evokai_knowledge_chunks SET embedding_v = CAST(:v AS {self.ns}.vector), "  # ruff: ignore[hardcoded-sql-expression]
            "tsv = to_tsvector(CAST(:cfg AS regconfig), coalesce(section, '') || ' ' || text) "
            "WHERE id = :id"
        )
        await s.execute(
            stmt,
            [
                {"v": vector_literal(v), "cfg": language, "id": r.id}
                for r, v in zip(rows, vectors, strict=True)
            ],
        )

    async def vector_search(
        self,
        kb_ids: Sequence[str],
        dims: int,
        query: Sequence[float],
        doc_ids: Sequence[str] | None,
        limit: int,
    ) -> list[Hit]:
        """The closest passages by cosine distance, through the HNSW index of the dimensions.

        Returns:
            Hits with their cosine similarity, best first.
        """
        extra, params = self._filter(doc_ids)
        vec = f"{self.ns}.vector({int(dims)})"
        # Only trusted parts are formatted in: the schema name, integer sizes, fixed filters.
        stmt = sa.text(
            f"SELECT id, 1 - ((embedding_v::{vec}) OPERATOR({self.ns}.<=>) CAST(:q AS {vec})) "  # ruff: ignore[hardcoded-sql-expression]
            f"FROM evokai_knowledge_chunks WHERE dims = {int(dims)} AND kb_id = ANY(:kbs){extra} "
            f"ORDER BY (embedding_v::{vec}) OPERATOR({self.ns}.<=>) CAST(:q AS {vec}) LIMIT :n"
        )
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    stmt, {"q": vector_literal(query), "kbs": list(kb_ids), "n": limit, **params}
                )
            ).all()
        return [Hit(str(cid), float(score)) for cid, score in rows]

    async def text_search(
        self,
        kb_ids: Sequence[str],
        query: str,
        language: str,
        doc_ids: Sequence[str] | None,
        limit: int,
    ) -> list[Hit]:
        """Full-text matches ranked by cover density (any of the query's words).

        Returns:
            Hits with their rank, best first.
        """
        words = re.findall(r"\w{2,}", query.lower())[:32]
        if not words:
            return []
        extra, params = self._filter(doc_ids)
        # Only trusted parts are formatted in: the schema name, integer sizes, fixed filters.
        stmt = sa.text(
            "SELECT id, ts_rank_cd(tsv, q) AS r FROM evokai_knowledge_chunks, "  # ruff: ignore[hardcoded-sql-expression]
            "to_tsquery(CAST(:cfg AS regconfig), :q) AS q "
            f"WHERE kb_id = ANY(:kbs){extra} AND tsv @@ q ORDER BY r DESC LIMIT :n"
        )
        tsq = " | ".join(re.sub(r"\W", "", w) for w in words)
        async with self.db.session() as s:
            rows = (
                await s.execute(
                    stmt, {"cfg": language, "q": tsq, "kbs": list(kb_ids), "n": limit, **params}
                )
            ).all()
        return [Hit(str(cid), float(score)) for cid, score in rows]

    async def vectors(self, chunk_ids: Sequence[str]) -> dict[str, list[float]]:
        """The embeddings of some passages.

        Returns:
            Chunk id to vector.
        """
        # Only trusted parts are formatted in: the schema name, integer sizes, fixed filters.
        stmt = sa.text(
            "SELECT id, embedding_v::text FROM evokai_knowledge_chunks WHERE id = ANY(:ids)"
        )
        async with self.db.session() as s:
            rows = (await s.execute(stmt, {"ids": list(chunk_ids)})).all()
        return {str(cid): [float(x) for x in str(v).strip("[]").split(",")] for cid, v in rows if v}


async def pgvector_schema(conn: Any) -> str | None:
    """The schema of the ``vector`` extension, when it is installed.

    Returns:
        Its schema, or None.
    """
    q = sa.text(
        "SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
        "WHERE e.extname = 'vector'"
    )
    row = (await conn.execute(q)).first()
    return str(row[0]) if row else None


async def choose_index(db: Database) -> Index:
    """The index this database supports.

    Returns:
        pgvector when the extension and the migration's columns are there, else the portable one.
    """
    if db.engine.dialect.name != "postgresql":
        return Index(db)
    try:
        async with db.engine.connect() as conn:
            ns = await pgvector_schema(conn)
            q = sa.text(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = 'evokai_knowledge_chunks' AND column_name = 'embedding_v'"
            )
            has_column = (await conn.execute(q)).first() is not None
    except Exception as e:
        log.warning("knowledge_index_check_failed", extra={"error_type": type(e).__name__})
        return Index(db)
    if ns and has_column:
        return PgVectorIndex(db, ns)
    log.warning("knowledge_without_pgvector", extra={"extension": bool(ns), "column": has_column})
    return Index(db)


class KnowledgeRepository:
    """Bases, documents, files and passages of applications."""

    def __init__(self, db: Database) -> None:
        """Bind to the database.

        Args:
            db: The database.
        """
        self.db = db
        self._index: Index | None = None

    async def index(self) -> Index:
        """The search index (chosen once per process).

        Returns:
            The index.
        """
        if self._index is None:
            self._index = await choose_index(self.db)
        return self._index

    # bases

    async def bases(self, application_id: str) -> list[m.KnowledgeBase]:
        """The application's bases.

        Returns:
            Bases by name.
        """
        b = m.KnowledgeBase
        async with self.db.session() as s:
            q = sa.select(b).where(b.application_id == application_id).order_by(b.name)
            return list((await s.execute(q)).scalars().all())

    async def base(self, application_id: str, name: str) -> m.KnowledgeBase | None:
        """A base by name.

        Returns:
            The base, or None.
        """
        b = m.KnowledgeBase
        async with self.db.session() as s:
            q = sa.select(b).where(b.application_id == application_id, b.name == name)
            return (await s.execute(q)).scalar_one_or_none()

    async def base_by_id(self, kb_id: str) -> m.KnowledgeBase | None:
        """A base by id.

        Returns:
            The base, or None.
        """
        async with self.db.session() as s:
            return await s.get(m.KnowledgeBase, kb_id)

    async def put_base(self, application_id: str, name: str, **fields: Any) -> m.KnowledgeBase:
        """Create a base or change its settings.

        Returns:
            The base.
        """
        b = m.KnowledgeBase
        async with self.db.session() as s:
            q = sa.select(b).where(b.application_id == application_id, b.name == name)
            row = (await s.execute(q)).scalar_one_or_none()
            if row is None:
                row = b(id=new_id(), application_id=application_id, name=name, **fields)
                s.add(row)
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
                row.updated_at = utcnow()
            await s.flush()
            return row

    async def delete_base(self, kb: m.KnowledgeBase) -> None:
        """Delete a base with its documents, files and passages."""
        async with self.db.session() as s:
            docs = sa.select(m.KnowledgeDocument.id).where(m.KnowledgeDocument.kb_id == kb.id)
            await s.execute(sa.delete(m.KnowledgeFile).where(m.KnowledgeFile.document_id.in_(docs)))
            await s.execute(sa.delete(m.KnowledgeChunk).where(m.KnowledgeChunk.kb_id == kb.id))
            await s.execute(
                sa.delete(m.KnowledgeDocument).where(m.KnowledgeDocument.kb_id == kb.id)
            )
            await s.execute(sa.delete(m.KnowledgeBase).where(m.KnowledgeBase.id == kb.id))

    async def counts(self, application_id: str) -> dict[str, dict[str, int]]:
        """Documents per base and status, and passages per base.

        Returns:
            Base id to {status: count, chunks: count}.
        """
        d = m.KnowledgeDocument
        out: dict[str, dict[str, int]] = {}
        async with self.db.session() as s:
            q = (
                sa
                .select(
                    d.kb_id, d.status, sa.func.count(), sa.func.coalesce(sa.func.sum(d.chunks), 0)
                )
                .where(d.application_id == application_id)
                .group_by(d.kb_id, d.status)
            )
            for kb_id, status, n, chunks in (await s.execute(q)).all():
                row = out.setdefault(kb_id, {"chunks": 0})
                row[str(status)] = int(n)
                row["chunks"] += int(chunks)
        return out

    # documents

    async def documents(
        self, kb_id: str, *, status: str | None = None, limit: int = 1000
    ) -> list[m.KnowledgeDocument]:
        """A base's documents.

        Returns:
            Documents, the latest first.
        """
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            q = sa.select(d).where(d.kb_id == kb_id)
            if status:
                q = q.where(d.status == status)
            q = q.order_by(d.updated_at.desc()).limit(limit)
            return list((await s.execute(q)).scalars().all())

    async def document(self, document_id: str) -> m.KnowledgeDocument | None:
        """A document by id.

        Returns:
            The document, or None.
        """
        async with self.db.session() as s:
            return await s.get(m.KnowledgeDocument, document_id)

    async def by_source(self, kb_id: str, source: str) -> m.KnowledgeDocument | None:
        """A base's document by its source.

        Returns:
            The document, or None.
        """
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            q = sa.select(d).where(d.kb_id == kb_id, d.source == source)
            return (await s.execute(q)).scalar_one_or_none()

    async def by_checksum(self, kb_id: str, checksum: str) -> m.KnowledgeDocument | None:
        """A base's document with the same content.

        Returns:
            The document, or None.
        """
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            q = sa.select(d).where(d.kb_id == kb_id, d.checksum == checksum).limit(1)
            return (await s.execute(q)).scalars().first()

    async def save_document(self, doc: m.KnowledgeDocument, data: bytes | None = None) -> None:
        """Insert or update a document, and replace its file when data is given."""
        async with self.db.session() as s:
            doc.updated_at = utcnow()
            await s.merge(doc)
            if data is not None:
                await s.execute(
                    sa.delete(m.KnowledgeFile).where(m.KnowledgeFile.document_id == doc.id)
                )
                s.add(
                    m.KnowledgeFile(
                        document_id=doc.id, application_id=doc.application_id, data=data
                    )
                )

    async def set_status(
        self, document_id: str, status: str, reason: str | None = None, **fields: Any
    ) -> None:
        """Change a document's ingestion status."""
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            await s.execute(
                sa
                .update(d)
                .where(d.id == document_id)
                .values(status=status, reason=reason, updated_at=utcnow(), **fields)
            )

    async def file(self, document_id: str) -> bytes | None:
        """A document's original file.

        Returns:
            The bytes, or None.
        """
        async with self.db.session() as s:
            row = await s.get(m.KnowledgeFile, document_id)
            return row.data if row else None

    async def pending(self) -> list[m.KnowledgeDocument]:
        """Documents left queued or half-ingested (by a process that stopped).

        Returns:
            Documents, oldest first.
        """
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            q = sa.select(d).where(d.status.in_(("queued", "ingesting"))).order_by(d.updated_at)
            return list((await s.execute(q)).scalars().all())

    async def replace_chunks(
        self,
        doc: m.KnowledgeDocument,
        kb: m.KnowledgeBase,
        chunks: list[Chunk],
        vectors: list[list[float]],
        **fields: Any,
    ) -> None:
        """Swap a document's passages for new ones and mark it ready, in one transaction.

        A failure leaves the previous passages in place.
        """
        index = await self.index()
        rows = [
            m.KnowledgeChunk(
                id=new_id(),
                application_id=doc.application_id,
                kb_id=kb.id,
                document_id=doc.id,
                position=c.position,
                text=c.text,
                kind=c.kind,
                page=c.page,
                page_end=c.page_end,
                section=c.section[:500],
                headings=c.headings,
                char_start=c.char_start,
                char_end=c.char_end,
                tokens=c.tokens,
                dims=len(vectors[i]) if vectors else 0,
            )
            for i, c in enumerate(chunks)
        ]
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            await s.execute(
                sa.delete(m.KnowledgeChunk).where(m.KnowledgeChunk.document_id == doc.id)
            )
            await index.write(s, rows, vectors, kb.language)
            await s.execute(
                sa
                .update(d)
                .where(d.id == doc.id)
                .values(
                    status="ready",
                    reason=None,
                    chunks=len(rows),
                    updated_at=utcnow(),
                    ingested_at=utcnow(),
                    **fields,
                )
            )

    async def delete_document(self, doc: m.KnowledgeDocument) -> None:
        """Delete a document with its file and passages."""
        async with self.db.session() as s:
            await s.execute(
                sa.delete(m.KnowledgeChunk).where(m.KnowledgeChunk.document_id == doc.id)
            )
            await s.execute(sa.delete(m.KnowledgeFile).where(m.KnowledgeFile.document_id == doc.id))
            await s.execute(sa.delete(m.KnowledgeDocument).where(m.KnowledgeDocument.id == doc.id))

    async def chunks(self, chunk_ids: Sequence[str]) -> list[m.KnowledgeChunk]:
        """Passages by id.

        Returns:
            The passages found.
        """
        c = m.KnowledgeChunk
        async with self.db.session() as s:
            q = sa.select(c).where(c.id.in_(list(chunk_ids)))
            rows = list((await s.execute(q)).scalars().all())
        for r in rows:
            r.embedding = None  # never carried around
        return rows

    async def document_chunks(self, document_id: str, limit: int = 500) -> list[m.KnowledgeChunk]:
        """A document's passages in order (for the console).

        Returns:
            Passages.
        """
        c = m.KnowledgeChunk
        async with self.db.session() as s:
            q = sa.select(c).where(c.document_id == document_id).order_by(c.position).limit(limit)
            rows = list((await s.execute(q)).scalars().all())
        for r in rows:
            r.embedding = None
        return rows

    async def readable(self, kb_ids: Sequence[str]) -> list[m.KnowledgeDocument]:
        """The ready documents of some bases (for access and metadata filters).

        Returns:
            Documents.
        """
        d = m.KnowledgeDocument
        async with self.db.session() as s:
            q = sa.select(d).where(d.kb_id.in_(list(kb_ids)), d.status == "ready")
            return list((await s.execute(q)).scalars().all())

    async def purge(self, now: datetime | None = None) -> int:
        """Delete documents older than their base's retention.

        Returns:
            How many documents were deleted.
        """
        now = now or utcnow()
        b, d = m.KnowledgeBase, m.KnowledgeDocument
        deleted = 0
        async with self.db.session() as s:
            bases = (await s.execute(sa.select(b).where(b.retention_days.is_not(None)))).scalars()
            for kb in list(bases):
                cutoff = now - timedelta(days=int(kb.retention_days or 0))
                ids = sa.select(d.id).where(d.kb_id == kb.id, d.updated_at < cutoff)
                found = [r for (r,) in (await s.execute(ids)).all()]
                if not found:
                    continue
                await s.execute(
                    sa.delete(m.KnowledgeChunk).where(m.KnowledgeChunk.document_id.in_(found))
                )
                await s.execute(
                    sa.delete(m.KnowledgeFile).where(m.KnowledgeFile.document_id.in_(found))
                )
                await s.execute(sa.delete(d).where(d.id.in_(found)))
                deleted += len(found)
        return deleted
