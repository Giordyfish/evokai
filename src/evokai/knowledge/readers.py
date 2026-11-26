"""Document readers with text recognition: scanned PDFs the local extractor cannot read.

The local extractor (``evokai.knowledge.extract``) reads every format's text layer in process. A
PDF without one (a scan) raises ``NoTextLayer``; a deployment that sets ``EVOKAI_DOCUMENT_READER``
sends it to one of these readers instead, when the application's data protection profile allows
the reader's processor (``evokai.protection.processor_allows``):

- ``azure``: Azure AI Document Intelligence, the ``prebuilt-layout`` model with Markdown output
  (REST API 2024-11-30). The resource key, or without one a Microsoft Entra ID token of the managed
  identity (``evokai.core.azure``). The analyze result is deleted as soon as it is read (the service
  would otherwise keep the document and the result for 24 hours).
  https://learn.microsoft.com/en-us/rest/api/aiservices/document-models/analyze-document?view=rest-aiservices-v4.0%20(2024-11-30)
  https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/document-intelligence/data-privacy-security
- ``docling``: a self-hosted docling-serve (``POST /v1/convert/file``, Markdown output, OCR on),
  with its ``X-Api-Key`` when it has one.
  https://github.com/docling-project/docling-serve/blob/main/docs/usage.md

Both answer Markdown with a page break marker; it is read with the Markdown extractor page by page
(Document Intelligence writes tables as HTML, read with the HTML extractor), so headings, tables
and pages reach the chunker as they do for any other document.

Not verified against a live Azure account or a live docling-serve.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from typing import Any, Protocol

import httpx

from evokai.core.errors import ConfigError
from evokai.knowledge.extract import Block, Extracted, Unreadable, html_blocks, markdown_blocks

PAGE_BREAK = "<!-- PageBreak -->"
"""Document Intelligence's page break in Markdown; docling-serve is asked for the same."""
_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FIGURE_TAG = re.compile(r"</?figure[^>]*>|</?figcaption[^>]*>", re.I)

PROCESSORS = {"azure": "azure-document-intelligence", "docling": "docling"}
"""Readers and their entries in the provider catalog."""


class Reader(Protocol):
    """Reads a document that has no text layer."""

    processor: str

    async def read(self, name: str, mime: str | None, data: bytes) -> Extracted:
        """Recognise a document's text.

        Returns:
            Its blocks and pages.
        """
        ...


def from_markdown(markdown: str) -> Extracted:
    """A reader's Markdown as blocks, page by page (HTML tables read as tables).

    Returns:
        The blocks, with their pages, and the page count.

    Raises:
        Unreadable: Nothing but headings, even with text recognition.
    """
    blocks: list[Block] = []
    pages = markdown.split(PAGE_BREAK)
    title = None
    for number, page in enumerate(pages, 1):
        page = _FIGURE_TAG.sub("", _COMMENT.sub("", page))
        at = 0
        for m in [*_TABLE.finditer(page), None]:
            end = m.start() if m else len(page)
            part = markdown_blocks(page[at:end])
            title = title or part.title
            found = part.blocks
            if m is not None:
                found = found + html_blocks(m.group(0)).blocks
                at = m.end()
            for b in found:
                b.page = number
            blocks += found
    blocks = [b for b in blocks if b.text.strip()]
    if not any(b.kind != "heading" for b in blocks):
        raise Unreadable("the document has no text, even with text recognition")
    return Extracted(blocks, title, len(pages))


class DocumentIntelligence:
    """Azure AI Document Intelligence ``prebuilt-layout`` (REST 2024-11-30, Markdown output)."""

    processor = "azure-document-intelligence"
    API_VERSION = "2024-11-30"

    def __init__(
        self,
        endpoint: str,
        key: str | None,
        http: httpx.AsyncClient,
        *,
        model: str = "prebuilt-layout",
        timeout: float = 120.0,
        poll: float = 1.0,
    ) -> None:
        """Bind the resource.

        Args:
            endpoint: ``https://<resource>.cognitiveservices.azure.com``.
            key: Its key, or None for an Entra ID token (Cognitive Services User role).
            http: The HTTP client.
            model: The analysis model.
            timeout: Seconds the whole analysis may take.
            poll: Seconds between two polls when the service does not say (``Retry-After``).
        """
        self.endpoint = endpoint.rstrip("/")
        self.key = key
        self.http = http
        self.model = model
        self.timeout = timeout
        self.poll = poll
        self._token: Any = None

    async def _headers(self) -> dict[str, str]:
        if self.key:
            return {"Ocp-Apim-Subscription-Key": self.key}
        if self._token is None:
            from evokai.core.azure import COGNITIVE_SERVICES, token_provider

            self._token = token_provider(COGNITIVE_SERVICES)
        return {"Authorization": f"Bearer {await self._token()}"}

    async def read(self, name: str, mime: str | None, data: bytes) -> Extracted:  # ruff: ignore[unused-method-argument]
        """Analyse the document, wait for the result, delete it, read its Markdown.

        Returns:
            The blocks and pages.

        Raises:
            Unreadable: The service refused or failed the document, or took too long.
        """
        url = (
            f"{self.endpoint}/documentintelligence/documentModels/{self.model}:analyze"
            f"?api-version={self.API_VERSION}&outputContentFormat=markdown"
        )
        headers = await self._headers()
        body = {"base64Source": base64.b64encode(data).decode()}
        r = await self.http.post(url, json=body, headers=headers, timeout=30)
        if r.status_code != 202 or "operation-location" not in r.headers:
            raise Unreadable(f"text recognition refused the document (HTTP {r.status_code})")
        result_url = r.headers["operation-location"]
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                await asyncio.sleep(_retry_after(r, self.poll))
                r = await self.http.get(result_url, headers=await self._headers(), timeout=30)
                if r.status_code >= 400:
                    raise Unreadable(f"text recognition failed (HTTP {r.status_code})")
                doc = r.json()
                status = doc.get("status")
                if status == "succeeded":
                    content = (doc.get("analyzeResult") or {}).get("content") or ""
                    return from_markdown(content)
                if status in ("failed", "canceled"):
                    raise Unreadable("text recognition failed on the document")
                if time.monotonic() > deadline:
                    raise Unreadable("text recognition took too long")
        finally:
            # The service keeps the document and its result for 24 hours unless deleted.
            try:
                await self.http.delete(result_url, headers=await self._headers(), timeout=10)
            except httpx.HTTPError:
                pass


def _retry_after(r: httpx.Response, default: float) -> float:
    try:
        return min(max(float(r.headers.get("retry-after", default)), 0.0), 10.0)
    except ValueError:
        return default


class DoclingServe:
    """A self-hosted docling-serve: ``POST /v1/convert/file`` with OCR, Markdown out."""

    processor = "docling"

    def __init__(
        self, url: str, key: str | None, http: httpx.AsyncClient, *, timeout: float = 120.0
    ) -> None:
        """Bind the server.

        Args:
            url: Its base URL (``http://docling:5001``).
            key: Its ``DOCLING_SERVE_API_KEY``, sent as ``X-Api-Key``, or None.
            http: The HTTP client.
            timeout: Seconds a conversion may take.
        """
        self.url = url.rstrip("/")
        self.key = key
        self.http = http
        self.timeout = timeout

    async def read(self, name: str, mime: str | None, data: bytes) -> Extracted:
        """Convert the document.

        Returns:
            The blocks and pages.

        Raises:
            Unreadable: The server refused or failed the document.
        """
        r = await self.http.post(
            f"{self.url}/v1/convert/file",
            files={"files": (name, data, mime or "application/octet-stream")},
            data={"to_formats": "md", "do_ocr": "true", "md_page_break_placeholder": PAGE_BREAK},
            headers={"X-Api-Key": self.key} if self.key else {},
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise Unreadable(f"text recognition failed (HTTP {r.status_code})")
        doc = r.json()
        if doc.get("status") not in ("success", "partial_success"):
            raise Unreadable("text recognition failed on the document")
        return from_markdown((doc.get("document") or {}).get("md_content") or "")


def reader_from_settings(settings: Any, http: httpx.AsyncClient) -> Reader | None:
    """The deployment's reader (``EVOKAI_DOCUMENT_READER``), or None.

    Returns:
        The reader.

    Raises:
        ConfigError: The chosen reader lacks its endpoint.
    """
    kind = settings.document_reader
    if kind == "azure":
        if not settings.document_intelligence_endpoint:
            raise ConfigError(
                "EVOKAI_DOCUMENT_READER=azure needs EVOKAI_DOCUMENT_INTELLIGENCE_ENDPOINT"
            )
        return DocumentIntelligence(
            settings.document_intelligence_endpoint,
            settings.document_intelligence_key,
            http,
            model=settings.document_intelligence_model,
        )
    if kind == "docling":
        if not settings.docling_url:
            raise ConfigError("EVOKAI_DOCUMENT_READER=docling needs EVOKAI_DOCLING_URL")
        return DoclingServe(settings.docling_url, settings.docling_key, http)
    return None
