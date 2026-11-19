"""Embedding models: Amazon Titan Text Embeddings v2 on Bedrock, behind an allow-list.

An embedding model is usable when it matches the deployment's ``EVOKAI_EMBEDDING_ALLOW`` and the
application's data residency (``evokai.privacy.residency_allows``): an in-region Bedrock id such as
``amazon.titan-embed-text-v2:0`` runs in the deployment's region (``eu-north-1``), so an application
whose models must stay in the EU can use it, and nothing else reaches it.

``test:hash`` is a local model for tests and offline trials: hashed words and word pairs,
normalised. It costs nothing and finds passages that share words with the question.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import itertools
import math
import re
import struct
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from evokai.core.errors import BadRequest
from evokai.llm.catalog import normalize
from evokai.privacy import residency_allows

DIMENSIONS = (256, 512, 1024)
"""What Titan Text Embeddings v2 can produce."""
BATCH = 16
"""Texts embedded at the same time (Titan takes one text per call)."""

InputType = Literal["query", "document"]


@dataclass
class Embedded:
    """Vectors and the tokens they cost."""

    vectors: list[list[float]]
    tokens: int


class Embedder(Protocol):
    """Something that turns texts into vectors."""

    async def embed(self, texts: Sequence[str], kind: InputType) -> Embedded:
        """Embed texts.

        Returns:
            One vector per text and the input tokens.
        """
        ...


def _words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]{2,}", text)


def _stem(word: str) -> str:
    # A light, language-agnostic stem: plurals and verb endings of Italian and English fold.
    return word[:-1] if len(word) > 4 and word[-1] in "aeios" else word


@dataclass
class HashEmbedder:
    """``test:hash``: feature hashing of words and word pairs, normalised to unit length."""

    dimensions: int = 256

    def vector(self, text: str) -> list[float]:
        """One text's vector.

        Returns:
            A unit vector (all zeros for a text without words).
        """
        words = [_stem(w) for w in _words(text)]
        feats = words + [f"{a}_{b}" for a, b in itertools.pairwise(words)]
        v = [0.0] * self.dimensions
        for f in feats:
            h = hashlib.blake2b(f.encode(), digest_size=8).digest()
            i = int.from_bytes(h[:4], "little") % self.dimensions
            v[i] += 1.0 if h[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed(self, texts: Sequence[str], kind: InputType) -> Embedded:  # ruff: ignore[unused-method-argument]
        """Embed texts.

        Returns:
            Vectors and an estimate of the tokens.
        """
        return Embedded([self.vector(t) for t in texts], sum(max(1, len(t) // 4) for t in texts))


class BedrockEmbedder:
    """A Bedrock embedding model through pydantic-ai, in the deployment's region."""

    def __init__(self, model: str, dimensions: int, region: str) -> None:
        """Build the model.

        Args:
            model: ``bedrock:<id>``.
            dimensions: Vector size (Titan v2: 256, 512 or 1024).
            region: The deployment's AWS region.
        """
        from pydantic_ai.embeddings.bedrock import BedrockEmbeddingModel
        from pydantic_ai.providers.bedrock import BedrockProvider

        self.dimensions = dimensions
        self.model = BedrockEmbeddingModel(
            normalize(model).partition(":")[2], provider=BedrockProvider(region_name=region)
        )

    async def embed(self, texts: Sequence[str], kind: InputType) -> Embedded:
        """Embed texts, a batch at a time.

        Returns:
            Vectors and the tokens Bedrock counted.
        """
        vectors: list[list[float]] = []
        tokens = 0
        settings: Any = {"dimensions": self.dimensions, "bedrock_titan_normalize": True}
        for i in range(0, len(texts), BATCH):
            batch = [t[:30_000] for t in texts[i : i + BATCH]]
            r = await self.model.embed(batch, input_type=kind, settings=settings)
            vectors += [list(map(float, v)) for v in r.embeddings]
            tokens += r.usage.input_tokens or 0
        return Embedded(vectors, tokens)


class EmbeddingCatalog:
    """Embedding models allowed in this deployment, built once and reused."""

    def __init__(
        self,
        allow: list[str],
        region: str,
        factory: Callable[[str, int], Embedder] | None = None,
    ) -> None:
        """Create the catalogue.

        Args:
            allow: Glob patterns over ``provider:model``.
            region: The deployment's AWS region.
            factory: Builds an embedder from a model and dimensions (tests).
        """
        self.allow = allow
        self.region = region
        self._factory = factory
        self._built: dict[tuple[str, int], Embedder] = {}
        self._lock = asyncio.Lock()

    def allowed(self, model: str, residency: dict[str, Any] | None = None) -> bool:
        """Whether a model may embed this application's documents.

        Returns:
            True when the allow-list and the data residency both allow it.
        """
        name = normalize(model)
        if not any(fnmatch.fnmatchcase(name, p) for p in self.allow):
            return False
        return name.startswith("test:") or residency_allows(residency, name, self.region)

    def check(self, model: str, dimensions: int, residency: dict[str, Any] | None) -> str:
        """Validate a knowledge base's model and dimensions.

        Returns:
            The model with its provider.

        Raises:
            BadRequest: Not allowed here, or dimensions it cannot produce.
        """
        name = normalize(model)
        if not self.allowed(name, residency):
            raise BadRequest(
                f"The embedding model {name} is not allowed here (EVOKAI_EMBEDDING_ALLOW and the "
                "data residency).",
                code="embedding_model",
            )
        if name.startswith("bedrock:") and "titan-embed-text-v2" in name:
            if dimensions not in DIMENSIONS:
                raise BadRequest(
                    "Titan Text Embeddings v2 makes vectors of 256, 512 or 1024 dimensions.",
                    code="dimensions",
                )
        elif not 16 <= dimensions <= 4096:
            raise BadRequest("Dimensions go from 16 to 4096.", code="dimensions")
        return name

    def get(self, model: str, dimensions: int) -> Embedder:
        """The embedder of a model.

        Returns:
            The embedder.

        Raises:
            BadRequest: The model is not allowed here.
        """
        name = normalize(model)
        if not self.allowed(name):
            raise BadRequest(f"The embedding model {name} is not allowed here.", code="embedding")
        key = (name, dimensions)
        hit = self._built.get(key)
        if hit is not None:
            return hit
        if self._factory is not None:
            built = self._factory(name, dimensions)
        elif name.startswith("test:"):
            built = HashEmbedder(dimensions)
        elif name.startswith("bedrock:"):
            built = BedrockEmbedder(name, dimensions, self.region)
        else:
            raise BadRequest(f"No embedding provider for {name}.", code="embedding")
        self._built[key] = built
        return built

    def known(self) -> list[str]:
        """The concrete models of the allow-list (patterns left out), for the console.

        Returns:
            Model names.
        """
        concrete = [p for p in self.allow if not any(c in p for c in "*?[")]
        return concrete + (["test:hash"] if any(p.startswith("test:") for p in self.allow) else [])


def pack(vector: Sequence[float]) -> bytes:
    """A vector as float32 bytes (little endian), for the portable store.

    Returns:
        The bytes.
    """
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(data: bytes) -> list[float]:
    """A vector from its float32 bytes.

    Returns:
        The vector.
    """
    return list(struct.unpack(f"<{len(data) // 4}f", data))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity.

    Returns:
        -1 to 1 (0 when either vector is empty or zero).
    """
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
