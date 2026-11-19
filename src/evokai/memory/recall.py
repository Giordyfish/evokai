"""Which memories go into a turn's prompt: preferences always, the rest by relevance and recency.

- **Preferences** (how the person likes answers) are few and always apply: they are all recalled,
  newest first, up to ``MAX_PREFERENCES``.
- **Semantic facts and episodes** are ranked by ``relevance * (FLOOR + (1 - FLOOR) * decay)``:
  relevance is the cosine of the memory with the message (or their word overlap without
  embeddings), decay halves every ``half_life`` days since the memory was written or confirmed
  (episodes fade faster than facts). Memories less relevant than ``MIN_RELEVANCE`` are left out,
  except when there are no more than ``k`` of them: then every one fits and no query embedding is
  needed (they are ordered by recency).
- **De-duplication**: a candidate too close to one already picked is skipped, so the prompt never
  carries the same thing twice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from evokai.memory.consolidate import DUPLICATE_COSINE, content_words, jaccard, similarity

MAX_PREFERENCES = 12
DEFAULT_K = 6
"""Semantic facts and episodes per turn."""
MIN_RELEVANCE = {"vector": 0.3, "words": 0.1}
"""Least relevance for a memory to be recalled: by embeddings (unrelated short texts score about
0.1 to 0.25 with Titan v2) and by shared words."""
FLOOR = 0.35
"""What is left of an old memory's score: age lowers it, never erases it."""
HALF_LIFE_DAYS = {"semantic": 120.0, "episodic": 30.0, "preference": 365.0}


@dataclass
class Recallable:
    """A memory that may be recalled."""

    id: str
    kind: str
    text: str
    at: datetime
    """When it was written or last confirmed."""
    vector: list[float] | None = None


@dataclass(frozen=True)
class Recalled:
    """A memory picked for the prompt, with why."""

    id: str
    kind: str
    text: str
    score: float


def decay(at: datetime, now: datetime, half_life_days: float) -> float:
    """How much of a memory's weight is left after its age.

    Returns:
        1 for a memory written now, 0.5 after one half-life.
    """
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    age = max(0.0, (now - at).total_seconds() / 86_400)
    return math.pow(0.5, age / half_life_days)


def rank(
    memories: list[Recallable],
    *,
    query: str,
    query_vector: list[float] | None,
    now: datetime,
    k: int = DEFAULT_K,
) -> list[Recalled]:
    """Pick the memories for one turn.

    Returns:
        Preferences first (newest first), then the best semantic facts and episodes.
    """
    prefs = sorted(
        (x for x in memories if x.kind == "preference"), key=lambda x: x.at, reverse=True
    )
    out = [Recalled(x.id, x.kind, x.text, 1.0) for x in prefs[:MAX_PREFERENCES]]
    rest = [x for x in memories if x.kind != "preference"]
    if not rest or k <= 0:
        return out
    q_words = content_words(query)
    every_fits = len(rest) <= k
    scored: list[tuple[float, Recallable]] = []
    for x in rest:
        fade = decay(x.at, now, HALF_LIFE_DAYS.get(x.kind, 120.0))
        if every_fits:
            scored.append((fade, x))
            continue
        if query_vector and x.vector and len(query_vector) == len(x.vector):
            relevance, floor = similarity("", "", query_vector, x.vector), MIN_RELEVANCE["vector"]
        else:
            relevance, floor = jaccard(q_words, content_words(x.text)), MIN_RELEVANCE["words"]
        if relevance >= floor:
            scored.append((relevance * (FLOOR + (1 - FLOOR) * fade), x))
    scored.sort(key=lambda p: (p[0], p[1].at), reverse=True)
    picked: list[Recallable] = []
    for score, x in scored:
        if len(picked) >= k:
            break
        if any(_same(x, p) for p in picked):
            continue
        picked.append(x)
        out.append(Recalled(x.id, x.kind, x.text, round(score, 4)))
    return out


def _same(a: Recallable, b: Recallable) -> bool:
    if a.vector and b.vector:
        return similarity(a.text, b.text, a.vector, b.vector) >= DUPLICATE_COSINE
    return jaccard(content_words(a.text), content_words(b.text)) >= 0.8
