"""Retrieval: hybrid search fused by reciprocal rank, optional diversity, access and filters.

1. The documents a caller may read: the base's ``access`` and the document's ``access`` each match
   when empty or when they share a group with the caller; metadata filters narrow them further.
2. Two searches over those documents' passages: by meaning (cosine similarity of embeddings) and by
   words (full text: Postgres text search, or BM25 in the portable index).
3. Reciprocal rank fusion: each passage scores the sum of ``1 / (60 + rank)`` over the lists it
   is in. A passage found only by meaning must reach ``min_score`` (cosine similarity).
4. Optional maximal marginal relevance (``mmr``): passages are picked one by one, trading relevance
   for difference from those already picked (``mmr_lambda``), so near-duplicates give way.
5. The best ``top_k``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from evokai.knowledge.embeddings import cosine
from evokai.knowledge.store import Hit

RRF_K = 60


class RetrievalSettings(BaseModel):
    """How a knowledge base is searched."""

    top_k: int = Field(default=5, ge=1, le=20)
    min_score: float = Field(default=0.0, ge=0, le=1)
    """The cosine similarity a passage found only by meaning must reach (0 = no floor)."""
    hybrid: bool = True
    """Search by words too, fused with the search by meaning."""
    mmr: bool = False
    """Prefer passages that differ from each other (maximal marginal relevance)."""
    mmr_lambda: float = Field(default=0.7, ge=0, le=1)
    """1 = relevance only, 0 = difference only."""
    candidates: int = Field(default=40, ge=5, le=200)
    """Passages each search returns before fusion."""


def matches(access: Iterable[str] | None, groups: set[str] | None) -> bool:
    """Whether a caller's groups open a list of required groups.

    Args:
        access: Groups that may read (empty: everyone).
        groups: The caller's groups; None = no restriction (the console).

    Returns:
        True when readable.
    """
    required = {str(a) for a in access or []}
    return groups is None or not required or bool(required & groups)


def passes(meta: Mapping[str, Any] | None, filters: Mapping[str, Any] | None) -> bool:
    """Whether a document's metadata passes the filters.

    A filter value is a value (equal), a list (any of them) or ``{"gte": x, "lte": y}``.

    Returns:
        True when every filter passes.
    """
    meta = meta or {}
    for key, want in (filters or {}).items():
        have = meta.get(key)
        if isinstance(want, list):
            values = have if isinstance(have, list) else [have]
            if not any(v in want for v in values):
                return False
        elif isinstance(want, dict):
            try:
                if "gte" in want and not (have is not None and have >= want["gte"]):
                    return False
                if "lte" in want and not (have is not None and have <= want["lte"]):
                    return False
            except TypeError:
                return False
        elif isinstance(have, list):
            if want not in have:
                return False
        elif have != want:
            return False
    return True


def fuse(lists: Sequence[Sequence[Hit]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion.

    Returns:
        Chunk id to fused score.
    """
    out: dict[str, float] = {}
    for hits in lists:
        for rank, h in enumerate(hits, 1):
            out[h.chunk_id] = out.get(h.chunk_id, 0.0) + 1.0 / (k + rank)
    return out


def mmr(
    ranked: Sequence[str],
    relevance: Mapping[str, float],
    vectors: Mapping[str, Sequence[float]],
    n: int,
    lam: float,
) -> list[str]:
    """Maximal marginal relevance over fused candidates.

    Args:
        ranked: Candidates, best first.
        relevance: Their fused scores.
        vectors: Their embeddings (a candidate without one is treated as unlike the others).
        n: How many to pick.
        lam: Relevance against difference.

    Returns:
        The picked candidates in pick order.
    """
    if not ranked:
        return []
    top = max(relevance.values()) or 1.0
    rel = {c: relevance.get(c, 0.0) / top for c in ranked}
    picked: list[str] = []
    left = list(ranked)
    while left and len(picked) < n:
        best, best_score = left[0], float("-inf")
        for c in left:
            v = vectors.get(c)
            sim = max(
                (cosine(v, vectors[p]) for p in picked if v is not None and p in vectors),
                default=0.0,
            )
            score = lam * rel[c] - (1 - lam) * sim
            if score > best_score:
                best, best_score = c, score
        picked.append(best)
        left.remove(best)
    return picked


def relevance(score: float, lists: int) -> float:
    """A fused score as a share of the best possible one (first in every list).

    Returns:
        0 to 1.
    """
    best = lists / (RRF_K + 1)
    return round(min(score / best, 1.0), 4) if best else 0.0
