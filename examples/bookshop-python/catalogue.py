"""Inkwell Books catalogue and search function.

Reads the shared catalogue.json and searches it.
"""

import json
from pathlib import Path

CATALOGUE_PATH = Path(__file__).parent.parent / "bookshop-app" / "catalogue.json"
BOOKS: list[dict] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))


def search(query: str = "", in_stock_only: bool = False) -> list[dict]:
    """Search books whose title, author or genre contains every word of the query.

    Args:
        query: Words to look for; empty lists every book.
        in_stock_only: Only books with copies in stock.

    Returns:
        List of matching book dictionaries.
    """
    words = [w.strip() for w in query.lower().split() if w.strip()]

    results = []
    for b in BOOKS:
        if in_stock_only and b.get("stock", 0) == 0:
            continue
        searchable_text = f"{b.get('title', '')} {b.get('author', '')} {b.get('genre', '')}".lower()
        if all(w in searchable_text for w in words):
            results.append(b)
    return results
