"""Knowledge bases at work: ingestion in the background, search, citations and signed links.

**Ingestion** runs in the service's own process, a few documents at a time
(``EVOKAI_KNOWLEDGE_WORKERS``). Its state is durable: a document is ``queued``, then ``ingesting``,
then ``ready`` or ``failed`` with a reason, in the database. A process that stops leaves documents
queued or ingesting; the next one picks them up when it starts (from the kept file; a connector
file not downloaded yet is downloaded then). New passages replace the old ones in one transaction,
so a document stays searchable with its previous version until the new one is ready.

A file whose content did not change (same SHA-256) is not ingested again; a file identical to
another document of the base is refused as a duplicate. A connector's files are compared by their
modified time before anything is downloaded.

Embedding calls are usage records (kind ``embed``): the application's caps count them. Ingestion
records them under the subject ``system:knowledge``; a search under the subject that asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from evokai.core.errors import BadRequest, Conflict
from evokai.core.util import new_id, utcnow
from evokai.db import models as m
from evokai.db.repositories import Who
from evokai.knowledge.chunking import ChunkSettings, chunk
from evokai.knowledge.embeddings import EmbeddingCatalog
from evokai.knowledge.extract import MIME_OF, Unreadable, detect, extract
from evokai.knowledge.fetch import Fetcher, FetchRefused
from evokai.knowledge.search import (
    RetrievalSettings,
    fuse,
    matches,
    mmr,
    passes,
    relevance,
)
from evokai.llm.catalog import bare
from evokai.llm.prices import embedding_cost_micro_usd
from evokai.sessions.limits import cap_state

if TYPE_CHECKING:
    from evokai.services import Services

log = logging.getLogger("evokai.knowledge")

INGEST_SUBJECT = "system:knowledge"
DOC_CONNECTORS = ("google_drive", "microsoft_files")
SNIPPET_CHARS = 280


@dataclass
class Found:
    """A passage a search returned."""

    chunk: m.KnowledgeChunk
    document: m.KnowledgeDocument
    kb: m.KnowledgeBase
    score: float
    """Fused relevance, 0 to 1."""
    similarity: float | None = None
    """Cosine similarity with the question, when the search by meaning found it."""
    vector_rank: int | None = None
    text_rank: int | None = None

    @property
    def source(self) -> str:
        """Where it comes from, for spotlighting.

        Returns:
            ``knowledge:<base>/<document id>``.
        """
        return f"knowledge:{self.kb.name}/{self.document.id}"

    def location(self) -> dict[str, Any]:
        """Its place in the document.

        Returns:
            page (and pageEnd when it spans pages), section, charStart, charEnd.
        """
        c = self.chunk
        out: dict[str, Any] = {"charStart": c.char_start, "charEnd": c.char_end}
        if c.page is not None:
            out["page"] = c.page
            if c.page_end and c.page_end != c.page:
                out["pageEnd"] = c.page_end
        headings = list(c.headings or [])
        if headings and headings[0].strip() == (self.document.title or "").strip():
            headings = headings[1:]  # the document's own title is already the citation's title
        if headings:
            out["section"] = " > ".join(headings)
        return out

    def body(self) -> str:
        """The passage without the headings it starts with (they are in its location).

        Returns:
            The text.
        """
        lines = self.chunk.text.split("\n")
        heads = {h.strip() for h in self.chunk.headings or []}
        while lines and (not lines[0].strip() or lines[0].strip() in heads):
            lines.pop(0)
        return "\n".join(lines) or self.chunk.text


_MARKUP = re.compile(r"\*\*|__|`+|^#{1,6}\s+", re.M)
"""Markdown emphasis, code marks and heading marks: not part of what a reader sees."""


def snippet(text: str, limit: int = SNIPPET_CHARS) -> str:
    """The start of a passage, cut at a word.

    Returns:
        At most ``limit`` characters (an ellipsis when cut).
    """
    flat = " ".join(_MARKUP.sub("", text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return cut + "…"


def location_label(loc: dict[str, Any]) -> str:
    """A passage's place in words, for the model.

    Returns:
        e.g. ``p. 3, Art. 2``.
    """
    parts = []
    if "page" in loc:
        parts.append(f"p. {loc['page']}" + (f"-{loc['pageEnd']}" if "pageEnd" in loc else ""))
    if loc.get("section"):
        parts.append(str(loc["section"]))
    return ", ".join(parts)


def checksum(data: bytes) -> str:
    """The SHA-256 of a file.

    Returns:
        Hex digest.
    """
    return hashlib.sha256(data).hexdigest()


class KnowledgeService:
    """Ingestion workers, search and links of the process."""

    def __init__(self, svc: Services, catalog: EmbeddingCatalog | None = None) -> None:
        """Bind to the process services.

        Args:
            svc: Services (store, settings, HTTP client, keys).
            catalog: Embedding models (tests pass local ones).
        """
        self.svc = svc
        s = svc.settings
        self.catalog = catalog or EmbeddingCatalog(s.embedding_allow, s.aws_region)
        self.max_bytes = s.knowledge_max_file_mb * 1_000_000
        self.fetcher = Fetcher(svc.http, s.knowledge_fetch_allow, self.max_bytes)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.queued: set[str] = set()
        self.workers: list[asyncio.Task[None]] = []

    @property
    def repo(self) -> Any:
        """The knowledge repository.

        Returns:
            ``store.knowledge``.
        """
        return self.svc.store.knowledge

    # ── workers ──────────────────────────────────────────────────────────────────────────────────

    async def start(self) -> int:
        """Resume the documents a stopped process left queued or ingesting.

        Returns:
            How many were queued again.
        """
        pending = await self.repo.pending()
        for doc in pending:
            self.enqueue(doc.id)
        if pending:
            log.info("knowledge_resumed", extra={"documents": len(pending)})
        return len(pending)

    def _ensure_workers(self) -> None:
        self.workers = [w for w in self.workers if not w.done()]
        while len(self.workers) < self.svc.settings.knowledge_workers:
            self.workers.append(asyncio.create_task(self._worker()))

    def enqueue(self, document_id: str) -> None:
        """Queue a document for ingestion (once, however often it is asked)."""
        if document_id in self.queued:
            return
        self.queued.add(document_id)
        self.queue.put_nowait(document_id)
        self._ensure_workers()

    async def _worker(self) -> None:
        while True:
            doc_id = await self.queue.get()
            try:
                await self.process(doc_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("knowledge_worker_failed", extra={"error_type": type(e).__name__})
            finally:
                self.queued.discard(doc_id)
                self.queue.task_done()

    async def drain(self) -> None:
        """Wait until every queued document is done (tests and the CLI)."""
        await self.queue.join()

    async def close(self) -> None:
        """Stop the workers (documents in progress stay queued for the next start)."""
        for w in self.workers:
            w.cancel()
        for w in self.workers:
            with contextlib.suppress(BaseException):
                await w
        self.workers = []

    # ── adding documents ─────────────────────────────────────────────────────────────────────────

    def _check_size(self, data: bytes) -> None:
        if len(data) > self.max_bytes:
            raise BadRequest(
                f"The document is larger than {self.svc.settings.knowledge_max_file_mb} MB.",
                code="too_large",
            )
        if not data:
            raise BadRequest("The document is empty.", code="empty")

    async def add_file(
        self,
        kb: m.KnowledgeBase,
        *,
        name: str,
        mime: str | None,
        data: bytes,
        title: str | None = None,
        access: list[str] | None = None,
        meta: dict[str, Any] | None = None,
        source_kind: str = "upload",
        source: str | None = None,
        modified: str | None = None,
    ) -> tuple[m.KnowledgeDocument, str]:
        """Add or replace a document with its file, and queue it.

        Returns:
            The document and what happened: ``created``, ``updated`` or ``unchanged``.

        Raises:
            BadRequest: Empty, too large or of an unknown type.

        The same content already in the base as another document raises ``Conflict`` (409
        ``duplicate``, with the other document's id).
        """
        self._check_size(data)
        kind = detect(name, mime, data)
        if kind is None:
            raise BadRequest(
                f"{name}: PDF, Word, PowerPoint, HTML, Markdown, text and CSV files are read.",
                code="unsupported",
            )
        digest = checksum(data)
        src = (source or name)[:500]
        existing = await self.repo.by_source(kb.id, src)
        same = await self.repo.by_checksum(kb.id, digest)
        if same is not None and (existing is None or same.id != existing.id):
            dup = Conflict(
                f"The same content is already in this base as {same.source}.", code="duplicate"
            )
            dup.extra = {"documentId": same.id}  # ty: ignore[missing-attribute]
            raise dup
        if existing is not None and existing.checksum == digest and existing.status == "ready":
            changed = False
            if title is not None and title != existing.title:
                existing.title, changed = title[:300], True
            if access is not None and access != existing.access:
                existing.access, changed = access, True
            if meta is not None and meta != existing.meta:
                existing.meta, changed = meta, True
            if modified and modified != existing.modified:
                existing.modified, changed = modified, True
            if changed:
                await self.repo.save_document(existing)
            return existing, "unchanged"
        doc = existing or m.KnowledgeDocument(
            id=new_id(),
            application_id=kb.application_id,
            kb_id=kb.id,
            source=src,
            version=0,
            chunks=0,
            tokens=0,
            attempts=0,
            created_at=utcnow(),
            meta={},
            access=[],
        )
        doc.source_kind = source_kind
        doc.title = (title or doc.title or "")[:300]
        doc.mime = (mime or MIME_OF.get(kind) or "")[:120]
        doc.checksum = digest
        doc.size = len(data)
        doc.status, doc.reason, doc.attempts = "queued", None, 0
        if access is not None:
            doc.access = access
        if meta is not None:
            doc.meta = meta
        if modified is not None:
            doc.modified = modified
        await self.repo.save_document(doc, data)
        self.enqueue(doc.id)
        return doc, "updated" if existing else "created"

    async def add_url(
        self,
        kb: m.KnowledgeBase,
        url: str,
        *,
        title: str | None = None,
        access: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[m.KnowledgeDocument, str]:
        """Fetch a document by address and add it.

        Returns:
            The document and what happened (a refused address raises ``FetchRefused``).
        """
        fetched = await self.fetcher.fetch(url)
        return await self.add_file(
            kb,
            name=fetched.name,
            mime=fetched.mime,
            data=fetched.data,
            title=title,
            access=access,
            meta=meta,
            source_kind="url",
            source=url.strip(),
        )

    async def reingest(self, doc: m.KnowledgeDocument) -> None:
        """Ingest a document again from its source (an address or a connector file is fetched)."""
        await self.repo.set_status(doc.id, "queued", None, attempts=0)
        self.enqueue(doc.id)

    async def connector(self, application_id: str, server: str) -> Any:
        """A document connector registered by the application.

        Returns:
            The connector.

        Raises:
            BadRequest: No such server, or not a Google Drive or SharePoint connector.
        """
        from evokai.connectors import build

        rows = await self.svc.store.mcp_servers.all(application_id)
        row = next((r for r in rows if r.name == server), None)
        if row is None or row.connector not in DOC_CONNECTORS:
            raise BadRequest(
                f"{server} is not a Google Drive or SharePoint connector of this application.",
                code="source",
            )
        return build(row.name, row.connector, row.config, self.svc.http)

    async def sync(self, kb: m.KnowledgeBase) -> dict[str, int]:
        """Bring the base up to date with its sources (addresses and connectors).

        New and changed files are queued, unchanged ones skipped, files gone from a connector
        deleted.

        Returns:
            queued, unchanged, removed, failed.
        """
        out = {"queued": 0, "unchanged": 0, "removed": 0, "failed": 0}
        docs = await self.repo.documents(kb.id, limit=100_000)
        for src in kb.sources or []:
            kind = src.get("kind")
            if kind == "url":
                try:
                    _, what = await self.add_url(
                        kb, str(src["url"]), title=src.get("title"), access=src.get("access")
                    )
                except (FetchRefused, BadRequest, Conflict) as e:
                    out["failed"] += 1
                    log.info("knowledge_sync_url_failed", extra={"kb": kb.name, "error": e.code})
                    continue
                out["unchanged" if what == "unchanged" else "queued"] += 1
            elif kind == "connector":
                server = str(src.get("server") or "")
                try:
                    conn = await self.connector(kb.application_id, server)
                    files = await conn.list_documents()
                except Exception as e:
                    out["failed"] += 1
                    log.info(
                        "knowledge_sync_connector_failed",
                        extra={"kb": kb.name, "error_type": type(e).__name__},
                    )
                    continue
                seen = set()
                for f in files:
                    source = f"{server}:{f['id']}"[:500]
                    seen.add(source)
                    doc = next((d for d in docs if d.source == source), None)
                    if (
                        doc is not None
                        and doc.modified == f.get("modified")
                        and doc.status == "ready"
                    ):
                        out["unchanged"] += 1
                        continue
                    if doc is None:
                        doc = m.KnowledgeDocument(
                            id=new_id(),
                            application_id=kb.application_id,
                            kb_id=kb.id,
                            source=source,
                            source_kind="connector",
                            title=str(f.get("name") or "")[:300],
                            mime=str(f.get("mime") or "")[:120],
                            meta={"url": f["url"]} if f.get("url") else {},
                            access=list(src.get("access") or []),
                            version=0,
                            chunks=0,
                            tokens=0,
                            attempts=0,
                            created_at=utcnow(),
                        )
                    doc.status, doc.reason = "queued", None
                    doc.modified = f.get("modified")
                    await self.repo.save_document(doc)
                    self.enqueue(doc.id)
                    out["queued"] += 1
                for d in docs:
                    if d.source_kind == "connector" and d.source.startswith(f"{server}:"):
                        if d.source not in seen:
                            await self.repo.delete_document(d)
                            out["removed"] += 1
        return out

    # ── ingestion ────────────────────────────────────────────────────────────────────────────────

    async def _content(self, doc: m.KnowledgeDocument) -> tuple[str, str, bytes]:
        """The document's file: the one kept, else fetched (address) or downloaded (connector).

        An address is fetched when it is added and at every sync, so ingesting it again reads the
        kept file; a connector's file is downloaded here, after a sync found it new or changed.

        Returns:
            Name, type and bytes.

        Raises:
            Unreadable: The source cannot give it.
        """
        if doc.source_kind == "url":
            kept = await self.repo.file(doc.id)
            if kept is not None:
                return doc.source.rsplit("/", 1)[-1] or doc.source, doc.mime, kept
            fetched = await self.fetcher.fetch(doc.source)
            self._check_size(fetched.data)
            return fetched.name, fetched.mime, fetched.data
        if doc.source_kind == "connector":
            server, _, ref = doc.source.partition(":")
            conn = await self.connector(doc.application_id, server)
            try:
                name, mime, data = await conn.download(ref)
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
            self._check_size(data)
            return name, mime, data
        data = await self.repo.file(doc.id)
        if data is None:
            raise Unreadable("the original file is gone")
        return doc.source, doc.mime, data

    async def process(self, document_id: str) -> None:
        """Ingest one document: read, cut, embed, store. Any failure is the document's reason.

        Raises:
            CancelledError: The process is stopping (the document stays queued for the next one).
            Unreadable: Raised and caught inside: the document fails with its reason.
        """
        doc = await self.repo.document(document_id)
        if doc is None or doc.status not in {"queued", "ingesting"}:
            return
        kb = await self.repo.base_by_id(doc.kb_id)
        app = await self.svc.store.applications.get(doc.application_id)
        if kb is None or app is None:
            return
        doc.status, doc.reason, doc.attempts = "ingesting", None, (doc.attempts or 0) + 1
        await self.repo.set_status(doc.id, "ingesting", None, attempts=doc.attempts)
        started = time.perf_counter()
        try:
            name, mime, data = await self._content(doc)
            if doc.source_kind == "connector":
                # The downloaded file is kept, for links and for ingesting it again.
                await self.repo.save_document(
                    _with(
                        doc, checksum=checksum(data), size=len(data), mime=(mime or doc.mime)[:120]
                    ),
                    data,
                )
            extracted = extract(name, mime, data)
            parts = chunk(extracted, ChunkSettings.model_validate(kb.chunking or {}))
            if not parts:
                raise Unreadable("the document has no text")
            title = doc.title or extracted.title or name
            texts = ["\n".join(x for x in (title, p.section) if x) + "\n\n" + p.text for p in parts]
            caps = await cap_state(
                self.svc.store, app, Who(app.id, INGEST_SUBJECT), utcnow(), subject=False
            )
            estimate = embedding_cost_micro_usd(kb.embedding_model, sum(len(t) // 3 for t in texts))
            if caps.hit(estimate / 1e6):
                raise Unreadable("the application's spending cap is reached")
            embedder = self.catalog.get(kb.embedding_model, kb.dimensions)
            embedded = await embedder.embed(texts, "document")
            await self._record(
                app.id,
                INGEST_SUBJECT,
                [f"knowledge:{kb.name}"],
                kb.embedding_model,
                embedded.tokens,
                started,
            )
            await self.repo.replace_chunks(
                doc,
                kb,
                parts,
                embedded.vectors,
                title=title[:300],
                pages=extracted.pages,
                tokens=embedded.tokens,
                version=(doc.version or 0) + 1,
            )
            log.info(
                "knowledge_ingested",
                extra={
                    "kb": kb.name,
                    "documentId": doc.id,
                    "chunks": len(parts),
                    "tokens": embedded.tokens,
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )
        except asyncio.CancelledError:
            raise
        except (Unreadable, FetchRefused, BadRequest, PermissionError, LookupError) as e:
            reason = getattr(e, "message", None) or str(e)
            await self.repo.set_status(doc.id, "failed", reason[:300])
        except Exception as e:
            while isinstance(e, ExceptionGroup) and e.exceptions:
                e = e.exceptions[0]  # concurrent embedding calls: the first failure says why
            log.warning(
                "knowledge_ingest_failed",
                extra={"documentId": doc.id, "error_type": type(e).__name__},
            )
            await self.repo.set_status(
                doc.id, "failed", f"ingestion failed ({type(e).__name__})"[:300]
            )

    async def _record(
        self,
        application_id: str,
        subject: str,
        tags: list[str],
        model: str,
        tokens: int,
        started: float,
        **ids: Any,
    ) -> None:
        await self.svc.store.usage.record(
            Who(application_id, subject),
            tags,
            kind="embed",
            model=bare(model),
            input_tokens=tokens,
            cost_micro_usd=embedding_cost_micro_usd(model, tokens),
            latency_ms=round((time.perf_counter() - started) * 1000),
            **ids,
        )

    # ── search ───────────────────────────────────────────────────────────────────────────────────

    async def search(
        self,
        app: m.Application,
        kbs: Sequence[m.KnowledgeBase],
        query: str,
        *,
        groups: set[str] | None,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
        subject: str = INGEST_SUBJECT,
        tags: list[str] | None = None,
        agent: str = "",
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[Found]:
        """Hybrid search over some bases, as a caller with these groups.

        Args:
            app: The application.
            kbs: The bases to search (the caller's own access to each is checked here).
            query: The question.
            groups: The caller's groups (None: no restriction, for the console).
            filters: Metadata filters.
            top_k: How many passages (default: the first base's setting).
            subject: Who the query embedding is recorded for.
            tags: Tags of the usage record.
            agent: The agent asking.
            session_id: Its conversation.
            turn_id: Its turn.

        Returns:
            The passages, best first.
        """
        query = query.strip()[:2_000]
        kbs = [kb for kb in kbs if matches(kb.access, groups)]
        if not query or not kbs:
            return []
        settings = RetrievalSettings.model_validate(kbs[0].retrieval or {})
        k = min(max(top_k or settings.top_k, 1), 20)
        readable = await self.repo.readable([kb.id for kb in kbs])
        docs = {d.id: d for d in readable if matches(d.access, groups) and passes(d.meta, filters)}
        if not docs:
            return []
        every = len(docs) == len(readable)
        doc_ids = None if every else list(docs)
        index = await self.repo.index()
        lists = []
        sims: dict[str, float] = {}
        vranks: dict[str, int] = {}
        tranks: dict[str, int] = {}
        by_model: dict[tuple[str, int], list[m.KnowledgeBase]] = {}
        for kb in kbs:
            by_model.setdefault((kb.embedding_model, kb.dimensions), []).append(kb)
        for (model, dims), group in by_model.items():
            started = time.perf_counter()
            embedded = await self.catalog.get(model, dims).embed([query], "query")
            await self._record(
                app.id,
                subject,
                tags or [],
                model,
                embedded.tokens,
                started,
                agent_name=agent,
                session_id=session_id,
                turn_id=turn_id,
            )
            hits = await index.vector_search(
                [kb.id for kb in group], dims, embedded.vectors[0], doc_ids, settings.candidates
            )
            lists.append(hits)
            for rank, h in enumerate(hits, 1):
                sims[h.chunk_id] = h.score
                vranks[h.chunk_id] = rank
        if settings.hybrid:
            by_lang: dict[str, list[m.KnowledgeBase]] = {}
            for kb in kbs:
                by_lang.setdefault(kb.language, []).append(kb)
            for lang, group in by_lang.items():
                hits = await index.text_search(
                    [kb.id for kb in group], query, lang, doc_ids, settings.candidates
                )
                lists.append(hits)
                for rank, h in enumerate(hits, 1):
                    tranks[h.chunk_id] = rank
        fused = fuse(lists)
        fused = {
            c: s for c, s in fused.items() if c in tranks or sims.get(c, 0.0) >= settings.min_score
        }
        ranked = sorted(fused, key=lambda c: fused[c], reverse=True)
        if settings.mmr and len(ranked) > k:
            vectors = await index.vectors(ranked[: settings.candidates])
            picked = mmr(ranked[: settings.candidates], fused, vectors, k, settings.mmr_lambda)
            # MMR decides which passages; they are shown (and numbered) by relevance.
            ranked = sorted(picked, key=lambda c: fused[c], reverse=True)
        ranked = ranked[:k]
        rows = {c.id: c for c in await self.repo.chunks(ranked)}
        kb_of = {kb.id: kb for kb in kbs}
        out = []
        n_lists = 2 if settings.hybrid else 1
        for cid in ranked:
            c = rows.get(cid)
            if c is None or c.document_id not in docs:
                continue
            out.append(
                Found(
                    chunk=c,
                    document=docs[c.document_id],
                    kb=kb_of[c.kb_id],
                    score=relevance(fused[cid], n_lists),
                    similarity=round(sims[cid], 4) if cid in sims else None,
                    vector_rank=vranks.get(cid),
                    text_rank=tranks.get(cid),
                )
            )
        return out

    # ── links and citations ──────────────────────────────────────────────────────────────────────

    def _signature(self, document_id: str, expires: int) -> str:
        key = self.svc.keyring.signing_key.encode()
        msg = f"knowledge-file:{document_id}:{expires}".encode()
        return hmac.new(key, msg, hashlib.sha256).hexdigest()[:40]

    def signed_url(self, document_id: str, page: int | None = None) -> str:
        """A short-lived link to a document's original file.

        Returns:
            An absolute address (valid ``EVOKAI_KNOWLEDGE_LINK_TTL`` seconds).
        """
        s = self.svc.settings
        base = (s.public_url or f"http://localhost:{s.port}{s.base_path}").rstrip("/")
        expires = int(time.time()) + s.knowledge_link_ttl
        sig = self._signature(document_id, expires)
        anchor = f"#page={page}" if page else ""
        return f"{base}/v1/knowledge/files/{document_id}?e={expires}&s={sig}{anchor}"

    def verify(self, document_id: str, expires: int, sig: str) -> bool:
        """Whether a signed link is genuine and alive.

        Returns:
            True when valid.
        """
        if expires < time.time():
            return False
        return hmac.compare_digest(self._signature(document_id, expires), sig)

    def link(self, found: Found) -> str | None:
        """Where a citation points: the application's address of the document, or a signed link.

        Returns:
            The address, or None for a document without a kept file and without a template.
        """
        kb, doc, c = found.kb, found.document, found.chunk
        if kb.link_template:
            values = {
                "documentId": doc.id,
                "source": doc.source,
                "title": doc.title,
                "kb": kb.name,
                "page": "" if c.page is None else str(c.page),
                "section": c.section,
                **{f"meta.{k}": str(v) for k, v in (doc.meta or {}).items()},
            }
            out = kb.link_template
            for key, value in values.items():
                out = out.replace("{" + key + "}", quote(str(value), safe=""))
            return out
        if doc.source_kind == "url":
            return doc.source + (
                f"#page={c.page}" if c.page and doc.mime == "application/pdf" else ""
            )
        if doc.source_kind == "connector" and (doc.meta or {}).get("url"):
            return str(doc.meta["url"])
        return self.signed_url(doc.id, c.page if doc.mime == "application/pdf" else None)

    def citation(self, found: Found, index: int) -> dict[str, Any]:
        """The ``citation`` event of a passage.

        Returns:
            type, index, kb, documentId, title, location, snippet, url.
        """
        return {
            "type": "citation",
            "index": index,
            "kb": found.kb.name,
            "documentId": found.document.id,
            "title": found.document.title or found.document.source,
            "location": found.location(),
            "snippet": snippet(found.body()),
            "url": self.link(found),
        }


def _with(doc: m.KnowledgeDocument, **fields: Any) -> m.KnowledgeDocument:
    for k, v in fields.items():
        setattr(doc, k, v)
    return doc
