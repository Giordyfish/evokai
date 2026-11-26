"""Scanned documents read by Azure Document Intelligence or docling-serve, and reranking.

No Azure account and no docling-serve: every request goes to a mock transport shaped from the
Document Intelligence REST reference (2024-11-30), docling-serve's usage page and Cohere's v2
rerank reference.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from azure.core.credentials import AccessToken

from evokai.core import azure
from evokai.core.errors import ConfigError
from evokai.knowledge import readers
from evokai.knowledge.extract import NoTextLayer, Unreadable, extract
from evokai.knowledge.rerank import Reranker
from evokai.knowledge.service import KnowledgeService
from evokai.llm import providers as pc
from evokai.protection import DataProtection
from tests import knowledge_files as files
from tests.conftest import Env
from tests.test_knowledge import CODICE, KB, POLICY, _adm, _base, _ready

LAYOUT = """# Contratto di fornitura

<!-- PageHeader="Vita Energy" -->

Il cliente paga entro trenta giorni.

<!-- PageBreak -->

## Allegato A

<table>
<tr><th>Voce</th><th>Prezzo</th></tr>
<tr><td>Energia</td><td>0,12</td></tr>
</table>

<figure>Logo</figure>

Fine del documento.
"""


def test_document_intelligence_markdown_becomes_blocks_with_pages() -> None:
    out = readers.from_markdown(LAYOUT)
    assert out.pages == 2 and out.title == "Contratto di fornitura"
    kinds = [(b.kind, b.page) for b in out.blocks]
    assert ("heading", 1) in kinds and ("heading", 2) in kinds and ("table", 2) in kinds
    table = next(b for b in out.blocks if b.kind == "table")
    assert "Energia" in table.text and "0,12" in table.text
    assert not any("PageHeader" in b.text or "<figure>" in b.text for b in out.blocks)
    with pytest.raises(Unreadable, match="no text"):
        readers.from_markdown("# Only a title\n<!-- PageBreak -->\n")


class Credential:
    async def get_token(self, *scopes: str, **_: Any) -> AccessToken:
        return AccessToken(f"tok:{scopes[0]}", int(time.time()) + 3600)


def _di_service(seen: list[httpx.Request]) -> httpx.AsyncClient:
    polls = {"n": 0}
    result = "https://di.example/documentintelligence/documentModels/prebuilt-layout/analyzeResults/r1?api-version=2024-11-30"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(202, headers={"Operation-Location": result, "Retry-After": "0"})
        if request.method == "DELETE":
            return httpx.Response(204)
        polls["n"] += 1
        if polls["n"] == 1:
            return httpx.Response(200, json={"status": "running"}, headers={"Retry-After": "0"})
        return httpx.Response(
            200,
            json={
                "status": "succeeded",
                "analyzeResult": {"contentFormat": "markdown", "content": LAYOUT},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_document_intelligence_analyses_polls_and_deletes_the_result() -> None:
    seen: list[httpx.Request] = []
    di = readers.DocumentIntelligence("https://di.example/", "k", _di_service(seen), poll=0)
    out = await di.read("scan.pdf", "application/pdf", b"%PDF-scan")
    assert out.pages == 2
    post, first, second, delete = seen
    assert post.url.path == "/documentintelligence/documentModels/prebuilt-layout:analyze"
    assert post.url.params["api-version"] == "2024-11-30"
    assert post.url.params["outputContentFormat"] == "markdown"
    assert json.loads(post.content) == {"base64Source": "JVBERi1zY2Fu"}
    assert post.headers["ocp-apim-subscription-key"] == "k"
    assert first.method == second.method == "GET"
    # The service keeps the document for 24 hours unless the result is deleted.
    assert delete.method == "DELETE" and delete.url == second.url


async def test_document_intelligence_without_a_key_uses_the_managed_identity() -> None:
    azure.set_credential(Credential())
    try:
        seen: list[httpx.Request] = []
        di = readers.DocumentIntelligence("https://di.example", None, _di_service(seen), poll=0)
        await di.read("scan.pdf", "application/pdf", b"x")
        assert all(
            r.headers["authorization"] == f"Bearer tok:{azure.COGNITIVE_SERVICES}" for r in seen
        )
    finally:
        azure.set_credential(None)


async def test_document_intelligence_failures_are_reasons() -> None:
    def refused(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    di = readers.DocumentIntelligence(
        "https://di.example", "k", httpx.AsyncClient(transport=httpx.MockTransport(refused))
    )
    with pytest.raises(Unreadable, match="HTTP 401"):
        await di.read("scan.pdf", None, b"x")


async def test_docling_serve_converts_with_ocr_and_its_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        md = "# Bolletta\n\nImporto 42 euro.\n<!-- PageBreak -->\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        return httpx.Response(
            200, json={"status": "success", "document": {"md_content": md}, "errors": []}
        )

    dl = readers.DoclingServe(
        "http://docling:5001/", "secret", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    out = await dl.read("scan.pdf", "application/pdf", b"%PDF")
    assert out.pages == 2 and [b.kind for b in out.blocks] == ["heading", "text", "table"]
    (req,) = seen
    assert str(req.url) == "http://docling:5001/v1/convert/file"
    assert req.headers["x-api-key"] == "secret"
    body = req.content.decode(errors="replace")
    assert 'name="files"; filename="scan.pdf"' in body
    assert 'name="to_formats"' in body and 'name="do_ocr"' in body


def test_readers_from_settings() -> None:
    http = httpx.AsyncClient()

    def settings(**kw: Any) -> Any:
        base = {
            "document_reader": "none",
            "document_intelligence_endpoint": None,
            "document_intelligence_key": None,
            "document_intelligence_model": "prebuilt-layout",
            "docling_url": None,
            "docling_key": None,
        }
        return SimpleNamespace(**{**base, **kw})

    assert readers.reader_from_settings(settings(), http) is None
    with pytest.raises(ConfigError, match="DOCUMENT_INTELLIGENCE_ENDPOINT"):
        readers.reader_from_settings(settings(document_reader="azure"), http)
    with pytest.raises(ConfigError, match="DOCLING_URL"):
        readers.reader_from_settings(settings(document_reader="docling"), http)
    di = readers.reader_from_settings(
        settings(document_reader="azure", document_intelligence_endpoint="https://di"), http
    )
    assert isinstance(di, readers.DocumentIntelligence)


class FakeReader:
    processor = "azure-document-intelligence"

    def __init__(self) -> None:
        self.calls = 0

    async def read(self, *_: Any) -> Any:
        self.calls += 1
        return readers.from_markdown(LAYOUT)


_extract: Any = KnowledgeService._extract
"""The local-or-reader step, called with a stand-in service and a profile dict."""


def _app(**rule: Any) -> dict[str, Any]:
    return {"data_protection": DataProtection.model_validate(rule).stored() if rule else None}


async def test_a_scan_goes_to_the_reader_only_when_the_profile_allows_it() -> None:
    scan = files.pdf([None, None])
    with pytest.raises(NoTextLayer):
        extract("scan.pdf", "application/pdf", scan)
    reader = FakeReader()
    owner = SimpleNamespace(reader=reader)
    out = await _extract(owner, _app(), "scan.pdf", "application/pdf", scan)
    assert out.pages == 2 and reader.calls == 1
    # Document Intelligence is US-controlled: an EU jurisdiction keeps the scan away from it.
    with pytest.raises(Unreadable, match="does not allow text recognition"):
        await _extract(owner, _app(jurisdiction=["eu"]), "scan.pdf", None, scan)
    assert reader.calls == 1
    # A document with a text layer never leaves the process.
    text = files.pdf(["Il cliente paga entro trenta giorni dalla fattura ricevuta."])
    await _extract(owner, _app(), "a.pdf", "application/pdf", text)
    assert reader.calls == 1
    with pytest.raises(NoTextLayer):
        await _extract(SimpleNamespace(reader=None), _app(), "s.pdf", None, scan)


async def test_a_scan_is_ingested_through_the_reader(env: Env) -> None:
    env.svc.knowledge.reader = FakeReader()
    await _base(env)
    doc = await _ready(env, "scan.pdf", files.pdf([None, None]), mime="application/pdf")
    assert doc["status"] == "ready", doc
    r = await env.client.post(
        f"{KB}/norme/search", json={"query": "prezzo energia allegato"}, headers=_adm(env)
    )
    item = r.json()["items"][0]
    assert "Energia" in item["text"] and item["location"]["page"] == 2


# ── reranking ──


async def test_the_reranker_speaks_the_cohere_v2_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        n = len(json.loads(request.content)["documents"])
        return httpx.Response(
            200,
            json={
                "id": "r",
                "results": [{"index": i, "relevance_score": i / n} for i in reversed(range(n))],
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    endpoint = pc.Endpoint(
        id="ours", api="azure", base_url="https://res.services.ai.azure.com/providers/cohere/v2/"
    )
    azure.set_credential(Credential())
    try:
        rr = Reranker("azure:Cohere-rerank-v4.0-fast", endpoint)
        scores = await rr.rerank(http, "reclami", ["a", "b", "c"])
    finally:
        azure.set_credential(None)
    assert scores == [0.0, 1 / 3, 2 / 3]
    (req,) = seen
    assert str(req.url) == "https://res.services.ai.azure.com/providers/cohere/v2/rerank"
    assert json.loads(req.content) == {
        "model": "Cohere-rerank-v4.0-fast",
        "query": "reclami",
        "documents": ["a", "b", "c"],
        "top_n": 3,
    }
    assert req.headers["authorization"] == f"Bearer tok:{azure.COGNITIVE_SERVICES}"


async def test_a_failing_reranker_leaves_the_order_and_a_missing_endpoint_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    monkeypatch.setenv("VLLM_KEY", "k")
    endpoint = pc.Endpoint(
        id="v", api="openai-compatible", base_url="http://vllm:8000/v2", api_key_env="VLLM_KEY"
    )
    rr = Reranker("our-vllm:bge-reranker-v2-m3", endpoint)
    http = httpx.AsyncClient(transport=httpx.MockTransport(down))
    assert await rr.rerank(http, "q", ["a", "b"]) is None
    with pytest.raises(ConfigError, match="EVOKAI_PROVIDERS_FILE"):
        Reranker("our-vllm:x", None)
    assert Reranker.from_settings(SimpleNamespace(rerank_model=None)) is None


class ReverseReranker:
    """Scores the passages in the opposite of the fused order."""

    model = "test:rerank"

    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, _http: Any, _query: str, documents: list[str]) -> list[float]:
        self.calls += 1
        n = len(documents)
        return [(i + 1) / n for i in range(n)]


async def test_a_base_with_rerank_on_reorders_its_candidates(env: Env) -> None:
    rr = ReverseReranker()
    env.svc.knowledge.reranker = rr
    await _base(env, retrieval={"top_k": 3})
    await _ready(env, "condizioni.md", POLICY)
    await _ready(env, "codice.md", CODICE)
    query = {"query": "entro quanti giorni rispondono ai reclami?"}
    fused = (await env.client.post(f"{KB}/norme/search", json=query, headers=_adm(env))).json()
    assert rr.calls == 0 and "reclami" in fused["items"][0]["text"]

    await _base(env, retrieval={"top_k": 3, "rerank": True})
    got = (await env.client.post(f"{KB}/norme/search", json=query, headers=_adm(env))).json()
    assert rr.calls == 1
    assert got["items"][0]["text"] != fused["items"][0]["text"]
    assert got["items"][0]["score"] == pytest.approx(1.0)
