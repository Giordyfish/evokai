"""Knowledge bases of an application: settings, documents, sync, a test search, signed files.

Admin routes (admin key or a console login) under ``/v1/admin/knowledge``; the signed file route
``/v1/knowledge/files/{id}`` needs only the signature a citation carries.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from evokai import audit
from evokai.api.deps import admin_app, services
from evokai.core.errors import BadRequest, Forbidden, NotFound
from evokai.db import models as m
from evokai.identity.models import AppPrincipal
from evokai.knowledge.chunking import ChunkSettings
from evokai.knowledge.search import RetrievalSettings
from evokai.knowledge.service import DOC_CONNECTORS, Found
from evokai.knowledge.store import LANGUAGES
from evokai.protection import protection
from evokai.services import Services

router = APIRouter(prefix="/v1/admin/knowledge", tags=["knowledge"])
files = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
Svc = Annotated[Services, Depends(services)]
Admin = Annotated[AppPrincipal, Depends(admin_app)]

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
Group = Annotated[str, Field(min_length=1, max_length=100)]


class SourceDef(BaseModel):
    """Where a base's documents come from besides uploads, synced on demand."""

    kind: Literal["url", "connector"]
    url: str | None = Field(default=None, max_length=2_000)
    """``url``: the address of a document (on an allowed domain)."""
    server: str | None = Field(default=None, max_length=80)
    """``connector``: a Google Drive or SharePoint connector of the application (its MCP server
    name); every file of its folders becomes a document."""
    title: str | None = Field(default=None, max_length=300)
    access: list[Group] = Field(default_factory=list, max_length=50)
    """Access groups of the documents it brings."""


class KnowledgeBaseBody(BaseModel):
    """A knowledge base's settings."""

    title: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=1_000)
    """What it holds: the agent reads it to decide when to search."""
    embeddingModel: str | None = Field(default=None, max_length=120)
    """Default: the deployment's ``EVOKAI_EMBEDDING_MODEL`` (Titan Text Embeddings v2)."""
    dimensions: int = Field(default=1024, ge=16, le=4096)
    language: str = "simple"
    """Full-text configuration: simple, italian, english, french, german, spanish, portuguese,
    dutch."""
    chunking: ChunkSettings = Field(default_factory=ChunkSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    access: list[Group] = Field(default_factory=list, max_length=50)
    """Groups that may read the base (any of them); empty = everyone."""
    linkTemplate: str | None = Field(default=None, max_length=500)
    """The application's address of a document: ``{documentId}``, ``{source}``, ``{title}``,
    ``{page}``, ``{section}``, ``{kb}``, ``{meta.<field>}``. Without it, citations link a signed,
    short-lived address of the file kept by Evokai."""
    sources: list[SourceDef] = Field(default_factory=list, max_length=100)
    retentionDays: int | None = Field(default=None, ge=1, le=3_650)
    """Documents not updated for this many days are deleted; null = kept."""

    @field_validator("language")
    @classmethod
    def _language(cls, v: str) -> str:
        if v not in LANGUAGES:
            raise ValueError(f"language: one of {', '.join(LANGUAGES)}")
        return v

    @field_validator("linkTemplate")
    @classmethod
    def _link(cls, v: str | None) -> str | None:
        if v and not re.match(r"^(https?://|/)", v):
            raise ValueError("linkTemplate: an https address or a path")
        return v or None


class UrlBody(BaseModel):
    """A document to fetch by address."""

    url: str = Field(min_length=8, max_length=2_000)
    title: str | None = Field(default=None, max_length=300)
    access: list[Group] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None


class DocumentPatch(BaseModel):
    """A document's title, access groups and metadata."""

    title: str | None = Field(default=None, max_length=300)
    access: list[Group] | None = Field(default=None, max_length=50)
    metadata: dict[str, Any] | None = None


class SearchBody(BaseModel):
    """A test search."""

    query: str = Field(min_length=1, max_length=2_000)
    groups: list[Group] | None = Field(default=None, max_length=50)
    """Search as a caller with these groups; null = everything (no access filter)."""
    filters: dict[str, Any] | None = None
    topK: int | None = Field(default=None, ge=1, le=20)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def base_view(kb: m.KnowledgeBase, counts: dict[str, int] | None = None) -> dict[str, Any]:
    """A base for the console and the admin API.

    Returns:
        Its settings and document counts.
    """
    c = counts or {}
    return {
        "name": kb.name,
        "title": kb.title,
        "description": kb.description,
        "embeddingModel": kb.embedding_model,
        "dimensions": kb.dimensions,
        "language": kb.language,
        "chunking": ChunkSettings.model_validate(kb.chunking or {}).model_dump(),
        "retrieval": RetrievalSettings.model_validate(kb.retrieval or {}).model_dump(),
        "access": kb.access or [],
        "linkTemplate": kb.link_template,
        "sources": kb.sources or [],
        "retentionDays": kb.retention_days,
        "documents": sum(v for k, v in c.items() if k != "chunks"),
        "ready": c.get("ready", 0),
        "failed": c.get("failed", 0),
        "pending": c.get("queued", 0) + c.get("ingesting", 0),
        "chunks": c.get("chunks", 0),
        "updatedAt": _iso(kb.updated_at),
    }


def document_view(svc: Services, d: m.KnowledgeDocument) -> dict[str, Any]:
    """A document for the console.

    Returns:
        Its source, status and figures, and a signed link to its file when Evokai keeps one.
    """
    return {
        "id": d.id,
        "source": d.source,
        "sourceKind": d.source_kind,
        "title": d.title,
        "mime": d.mime,
        "size": d.size,
        "status": d.status,
        "reason": d.reason,
        "version": d.version,
        "metadata": d.meta or {},
        "access": d.access or [],
        "chunks": d.chunks,
        "pages": d.pages,
        "tokens": d.tokens,
        "updatedAt": _iso(d.updated_at),
        "ingestedAt": _iso(d.ingested_at),
        "url": svc.knowledge.signed_url(d.id) if d.source_kind != "connector" else None,
    }


async def _base(svc: Services, a: AppPrincipal, name: str) -> m.KnowledgeBase:
    kb = await svc.store.knowledge.base(a.application_id, name)
    if kb is None:
        raise NotFound("Unknown knowledge base.", code="unknown_knowledge_base")
    return kb


async def _document(svc: Services, kb: m.KnowledgeBase, document_id: str) -> m.KnowledgeDocument:
    doc = await svc.store.knowledge.document(document_id)
    if doc is None or doc.kb_id != kb.id:
        raise NotFound("Unknown document.", code="unknown_document")
    return doc


async def _app(svc: Services, a: AppPrincipal) -> m.Application:
    app = await svc.store.applications.get(a.application_id)
    if app is None:
        raise NotFound()
    return app


@router.get("")
async def list_bases(svc: Svc, a: Admin) -> dict[str, Any]:
    """The application's knowledge bases, with what a new one may use.

    Returns:
        items, embeddingModels (allowed here), defaultModel, languages, fetchAllowed (domains),
        connectors (document connectors of the application), maxFileMb.
    """
    app = await _app(svc, a)
    counts = await svc.store.knowledge.counts(app.id)
    bases = await svc.store.knowledge.bases(app.id)
    cat = svc.knowledge.catalog
    servers = await svc.store.mcp_servers.all(app.id)
    return {
        "items": [base_view(kb, counts.get(kb.id)) for kb in bases],
        "embeddingModels": [x for x in cat.known() if cat.allowed(x, protection(app))],
        "defaultModel": svc.settings.embedding_model,
        "languages": list(LANGUAGES),
        "fetchAllowed": svc.settings.knowledge_fetch_allow,
        "connectors": [s.name for s in servers if s.connector in DOC_CONNECTORS],
        "maxFileMb": svc.settings.knowledge_max_file_mb,
    }


@router.get("/{name}")
async def get_base(name: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """One base with its documents.

    Returns:
        The base and ``documents``.
    """
    kb = await _base(svc, a, name)
    counts = await svc.store.knowledge.counts(a.application_id)
    docs = await svc.store.knowledge.documents(kb.id)
    return {**base_view(kb, counts.get(kb.id)), "items": [document_view(svc, d) for d in docs]}


@router.put("/{name}")
async def put_base(
    name: str, body: KnowledgeBaseBody, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Create a base or change its settings.

    A new embedding model, new dimensions, chunking or language ingest every document again.

    Returns:
        The base and ``reingested`` (documents queued again).

    Raises:
        BadRequest: An invalid name, an embedding model not allowed here, or a source that is
            not a document connector.
    """
    if not NAME.match(name):
        raise BadRequest("A name: lower-case letters, digits, - and _.", code="name")
    app = await _app(svc, a)
    model = svc.knowledge.catalog.check(
        body.embeddingModel or svc.settings.embedding_model, body.dimensions, protection(app)
    )
    servers = {s.name: s for s in await svc.store.mcp_servers.all(app.id)}
    for src in body.sources:
        if src.kind == "url" and not src.url:
            raise BadRequest("A url source needs its url.", code="source")
        if src.kind == "connector":
            row = servers.get(src.server or "")
            if row is None or row.connector not in DOC_CONNECTORS:
                raise BadRequest(
                    f"{src.server} is not a Google Drive or SharePoint connector.", code="source"
                )
    old = await svc.store.knowledge.base(app.id, name)
    fields = {
        "title": body.title,
        "description": body.description,
        "embedding_model": model,
        "dimensions": body.dimensions,
        "language": body.language,
        "chunking": body.chunking.model_dump(),
        "retrieval": body.retrieval.model_dump(),
        "access": body.access,
        "link_template": body.linkTemplate,
        "sources": [s.model_dump(exclude_none=True) for s in body.sources],
        "retention_days": body.retentionDays,
    }
    before = base_view(old) if old else None
    kb = await svc.store.knowledge.put_base(app.id, name, **fields)
    reingest = 0
    if old is not None and (
        old.embedding_model != model
        or old.dimensions != body.dimensions
        or old.language != body.language
        or ChunkSettings.model_validate(old.chunking or {}) != body.chunking
    ):
        for d in await svc.store.knowledge.documents(kb.id, limit=100_000):
            await svc.knowledge.reingest(d)
            reingest += 1
    await audit.record(request, a, "knowledge.put", target=name, before=before, after=base_view(kb))
    return {**base_view(kb), "reingested": reingest}


@router.delete("/{name}")
async def delete_base(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, bool]:
    """Delete a base with every document, file and passage.

    Returns:
        ok.
    """
    kb = await _base(svc, a, name)
    await svc.store.knowledge.delete_base(kb)
    await audit.record(request, a, "knowledge.delete", target=name, before=base_view(kb))
    return {"ok": True}


def _list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    out = [x.strip() for v in values for x in v.split(",") if x.strip()]
    if len(out) > 50 or any(len(x) > 100 for x in out):
        raise BadRequest("At most 50 access groups of up to 100 characters.", code="access")
    return out


@router.post(
    "/{name}/documents",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload(
    name: str,
    request: Request,
    svc: Svc,
    a: Admin,
    filename: Annotated[str, Query(min_length=1, max_length=300)],
    title: Annotated[str | None, Query(max_length=300)] = None,
    access: Annotated[list[str] | None, Query()] = None,
    metadata: Annotated[str | None, Query(max_length=4_000)] = None,
) -> dict[str, Any]:
    """Upload a document (the request body is the file; its type in ``Content-Type``).

    An unchanged file (same name and content) is not ingested again; the same content under
    another name is refused (409 ``duplicate``). Ingestion runs in the background: poll the
    document's status.

    Returns:
        document, outcome (``created``, ``updated``, ``unchanged``).

    Raises:
        BadRequest: Metadata that is not a JSON object.
    """
    kb = await _base(svc, a, name)
    meta = None
    if metadata:
        try:
            meta = json.loads(metadata)
        except ValueError as e:
            raise BadRequest("metadata: a JSON object.", code="metadata") from e
        if not isinstance(meta, dict):
            raise BadRequest("metadata: a JSON object.", code="metadata")
    data = await request.body()
    doc, outcome = await svc.knowledge.add_file(
        kb,
        name=filename,
        mime=request.headers.get("content-type"),
        data=data,
        title=title,
        access=_list(access),
        meta=meta,
    )
    await audit.record(
        request,
        a,
        "knowledge.document.upload",
        target=f"{name}/{filename}",
        after={"documentId": doc.id, "size": len(data), "outcome": outcome},
    )
    return {"document": document_view(svc, doc), "outcome": outcome}


@router.post("/{name}/urls")
async def add_url(name: str, body: UrlBody, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Fetch a document by address (on a domain the deployment allows) and add it.

    Returns:
        document, outcome.
    """
    kb = await _base(svc, a, name)
    doc, outcome = await svc.knowledge.add_url(
        kb, body.url, title=body.title, access=body.access, meta=body.metadata
    )
    await audit.record(
        request, a, "knowledge.document.url", target=f"{name}/{body.url}"[:300],
        after={"documentId": doc.id, "outcome": outcome},
    )  # fmt: skip
    return {"document": document_view(svc, doc), "outcome": outcome}


@router.post("/{name}/sync")
async def sync(name: str, svc: Svc, a: Admin, request: Request) -> dict[str, Any]:
    """Re-sync the base's sources: new and changed files are ingested, removed ones deleted.

    Returns:
        queued, unchanged, removed, failed.
    """
    kb = await _base(svc, a, name)
    out = await svc.knowledge.sync(kb)
    await audit.record(request, a, "knowledge.sync", target=name, after=out)
    return out


@router.get("/{name}/documents/{document_id}")
async def get_document(name: str, document_id: str, svc: Svc, a: Admin) -> dict[str, Any]:
    """A document with its passages.

    Returns:
        The document and ``passages`` (text, place, size).
    """
    kb = await _base(svc, a, name)
    doc = await _document(svc, kb, document_id)
    rows = await svc.store.knowledge.document_chunks(doc.id)
    return {
        **document_view(svc, doc),
        "passages": [
            {
                "position": c.position,
                "kind": c.kind,
                "text": c.text,
                "page": c.page,
                "pageEnd": c.page_end,
                "section": c.section,
                "charStart": c.char_start,
                "charEnd": c.char_end,
                "tokens": c.tokens,
            }
            for c in rows
        ],
    }


@router.patch("/{name}/documents/{document_id}")
async def patch_document(
    name: str, document_id: str, body: DocumentPatch, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Change a document's title, access groups or metadata (no new ingestion).

    Returns:
        The document.
    """
    kb = await _base(svc, a, name)
    doc = await _document(svc, kb, document_id)
    before = document_view(svc, doc)
    if body.title is not None:
        doc.title = body.title
    if body.access is not None:
        doc.access = body.access
    if body.metadata is not None:
        doc.meta = body.metadata
    await svc.store.knowledge.save_document(doc)
    after = document_view(svc, doc)
    await audit.record(
        request, a, "knowledge.document.patch", target=f"{name}/{doc.source}"[:300],
        before={k: before[k] for k in ("title", "access", "metadata")},
        after={k: after[k] for k in ("title", "access", "metadata")},
    )  # fmt: skip
    return after


@router.post("/{name}/documents/{document_id}/reingest")
async def reingest(
    name: str, document_id: str, svc: Svc, a: Admin, request: Request
) -> dict[str, Any]:
    """Ingest a document again from its source.

    Returns:
        The document (queued).
    """
    kb = await _base(svc, a, name)
    doc = await _document(svc, kb, document_id)
    await svc.knowledge.reingest(doc)
    await audit.record(
        request, a, "knowledge.document.reingest", target=f"{name}/{doc.source}"[:300]
    )
    doc.status, doc.reason = "queued", None
    return document_view(svc, doc)


@router.delete("/{name}/documents/{document_id}")
async def delete_document(
    name: str, document_id: str, svc: Svc, a: Admin, request: Request
) -> dict[str, bool]:
    """Delete a document with its file and passages.

    Returns:
        ok.
    """
    kb = await _base(svc, a, name)
    doc = await _document(svc, kb, document_id)
    await svc.store.knowledge.delete_document(doc)
    await audit.record(request, a, "knowledge.document.delete", target=f"{name}/{doc.source}"[:300])
    return {"ok": True}


def found_view(svc: Services, f: Found, index: int) -> dict[str, Any]:
    """A search result for the console: the passage, its scores and its citation.

    Returns:
        index, score, similarity, vectorRank, textRank, text, document, location, citation.
    """
    return {
        "index": index,
        "score": f.score,
        "similarity": f.similarity,
        "vectorRank": f.vector_rank,
        "textRank": f.text_rank,
        "kind": f.chunk.kind,
        "text": f.chunk.text,
        "document": {"id": f.document.id, "title": f.document.title, "source": f.document.source},
        "location": f.location(),
        "citation": svc.knowledge.citation(f, index),
    }


@router.post("/{name}/search")
async def search(name: str, body: SearchBody, svc: Svc, a: Admin) -> dict[str, Any]:
    """Try a search: the ranked passages with their scores and how they would be cited.

    Returns:
        items, and the settings used.
    """
    kb = await _base(svc, a, name)
    app = await _app(svc, a)
    found = await svc.knowledge.search(
        app,
        [kb],
        body.query,
        groups=set(body.groups) if body.groups is not None else None,
        filters=body.filters,
        top_k=body.topK,
        subject="console:knowledge",
        tags=[f"knowledge:{kb.name}"],
    )
    return {
        "items": [found_view(svc, f, i) for i, f in enumerate(found, 1)],
        "retrieval": RetrievalSettings.model_validate(kb.retrieval or {}).model_dump(),
    }


INLINE = {"application/pdf", "text/plain"}


@files.get("/files/{document_id}", include_in_schema=False)
async def signed_file(
    document_id: str,
    svc: Svc,
    e: Annotated[int, Query()],
    s: Annotated[str, Query(max_length=64)],
) -> Response:
    """A document's original file, through the signed link of a citation.

    Returns:
        The file (PDF and text shown in the browser, anything else downloaded).

    Raises:
        Forbidden: The link is forged or expired.
        NotFound: The document or its file is gone.
    """
    if not svc.knowledge.verify(document_id, e, s):
        raise Forbidden("This link has expired.", code="link")
    doc = await svc.store.knowledge.document(document_id)
    data = await svc.store.knowledge.file(document_id) if doc else None
    if doc is None or data is None:
        raise NotFound()
    mime = doc.mime or "application/octet-stream"
    filename = re.sub(r"[^\w.\- ]", "_", doc.source.rsplit("/", 1)[-1])[:120] or "document"
    disposition = "inline" if mime in INLINE else "attachment"
    # The browser's PDF viewer does not run in a sandboxed document; nothing else needs more.
    csp = (
        "frame-ancestors 'none'"
        if mime == "application/pdf"
        else ("default-src 'none'; frame-ancestors 'none'; sandbox")
    )
    return Response(
        data,
        media_type=mime if mime in INLINE else "application/octet-stream",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "private, max-age=60",
            "Content-Security-Policy": csp,
        },
    )
