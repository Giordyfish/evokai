"""Deterministic consolidation of memories: duplicates merge, contradicted facts are superseded.

A new memory is compared with the person's active memories of the same group (preferences and
semantic facts together, episodes apart) by meaning (cosine of the embeddings, when both have one)
and by words (Jaccard of the content words):

- **duplicate**: the same thing said again (very similar). Nothing new is stored: the existing
  memory is refreshed and keeps the more informative wording.
- **supersede**: the same topic with a different detail, a negation that flips, or a number that
  changed («looks after the poetry shelf» then «now looks after the travel shelf»). The new
  memory is stored and the old one is marked ``superseded_by`` it: it is never recalled again.
- **new**: anything else.

No model call: the same input always gives the same decision.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from evokai.knowledge.embeddings import cosine

DUPLICATE_COSINE = 0.92
"""Embeddings this close say the same thing."""
TOPIC_COSINE = 0.72
"""Embeddings this close talk about the same topic (a change of detail supersedes)."""
DUPLICATE_WORDS = 0.8
"""Content-word overlap (Jaccard) of a restatement."""
TOPIC_WORDS = 0.45
"""Content-word overlap of two statements about the same topic."""

NEGATIONS = frozenset({
    "not",
    "no",
    "never",
    "longer",
    "dont",
    "doesnt",
    "isnt",
    "non",
    "mai",
    "piu",
    "nessun",
    "nessuna",
    "nicht",
    "kein",
    "keine",
    "nunca",
    "jamais",
    "pas",
    "plus",
    "ne",
})
STOP = frozenset({
    "the",
    "a",
    "an",
    "of",
    "to",
    "and",
    "or",
    "in",
    "on",
    "for",
    "with",
    "is",
    "are",
    "be",
    "user",
    "they",
    "he",
    "she",
    "il",
    "lo",
    "la",
    "i",
    "gli",
    "le",
    "un",
    "una",
    "uno",
    "di",
    "da",
    "del",
    "della",
    "dei",
    "delle",
    "e",
    "o",
    "su",
    "per",
    "con",
    "che",
    "utente",
    "ora",
    "now",
    "adesso",
    "does",
    "do",
    "did",
    "has",
    "have",
    "ha",
    "hanno",
    "always",
    "sempre",
})

Action = Literal["new", "duplicate", "supersede"]


def normalize_fact(text: str) -> str:
    """A fact as it is stored: single spaces, no trailing period, at most 1,000 characters.

    Returns:
        The clean text.
    """
    return re.sub(r"\s+", " ", text).strip().rstrip(".").strip()[:1000]


def words(text: str) -> list[str]:
    """Lower-case words without accents.

    Returns:
        The words.
    """
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", folded.replace("'", ""))


def _stem(word: str) -> str:
    # A light stem: plurals and verb endings of Italian and English fold («likes», «like»).
    return word[:-1] if len(word) > 4 and word[-1] in "aeios" else word


def content_words(text: str) -> set[str]:
    """The words that carry meaning (no stop words, no negations), lightly stemmed.

    Returns:
        The words.
    """
    return {_stem(w) for w in words(text) if w not in STOP and w not in NEGATIONS and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    """Overlap of two word sets.

    Returns:
        0 to 1.
    """
    return len(a & b) / len(a | b) if a and b else 0.0


def _negated(text: str) -> bool:
    return any(w in NEGATIONS for w in words(text))


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text))


def similarity(
    a_text: str, b_text: str, a_vec: list[float] | None, b_vec: list[float] | None
) -> float:
    """How alike two memories are: the cosine of their embeddings, else their word overlap.

    Returns:
        0 to 1.
    """
    if a_vec and b_vec and len(a_vec) == len(b_vec):
        return max(0.0, cosine(a_vec, b_vec))
    return jaccard(content_words(a_text), content_words(b_text))


@dataclass(frozen=True)
class Candidate:
    """An active memory to compare a new one with."""

    id: str
    text: str
    vector: list[float] | None = None


@dataclass(frozen=True)
class Decision:
    """What to do with a new memory."""

    action: Action
    target: str | None = None
    """The memory it duplicates or supersedes."""
    text: str = ""
    """The wording to keep (a duplicate keeps the more informative one)."""
    score: float = 0.0


def decide(text: str, vector: list[float] | None, existing: list[Candidate]) -> Decision:
    """Compare a new memory with the active ones and decide.

    Args:
        text: The new memory (normalised).
        vector: Its embedding, if any.
        existing: The person's active memories of the same group.

    Returns:
        The decision; the most similar memory wins.
    """
    new_words = content_words(text)
    best: tuple[float, float, Candidate] | None = None
    for c in existing:
        meaning = similarity(text, c.text, vector, c.vector)
        overlap = jaccard(new_words, content_words(c.text))
        key = max(meaning, overlap)
        if best is None or key > max(best[0], best[1]):
            best = (meaning, overlap, c)
    if best is None:
        return Decision("new", text=text)
    meaning, overlap, c = best
    by_vectors = bool(vector and c.vector)
    flipped = _negated(text) != _negated(c.text)
    changed_numbers = _numbers(text) != _numbers(c.text) and bool(_numbers(text) | _numbers(c.text))
    old_words = content_words(c.text)
    # The same words, or one statement adding detail to the other: a merge, not a contradiction.
    same_words = bool(new_words and old_words) and (
        new_words <= old_words or old_words <= new_words
    )
    duplicate = (
        ((meaning >= DUPLICATE_COSINE if by_vectors else False) or overlap >= DUPLICATE_WORDS)
        and not flipped
        and not changed_numbers
    )
    if duplicate or (same_words and not flipped and not changed_numbers):
        keep = text if len(text) > len(c.text) else c.text
        return Decision("duplicate", c.id, keep, max(meaning, overlap))
    same_topic = (meaning >= TOPIC_COSINE if by_vectors else False) or overlap >= TOPIC_WORDS
    if same_topic:
        return Decision("supersede", c.id, text, max(meaning, overlap))
    return Decision("new", text=text, score=max(meaning, overlap))
