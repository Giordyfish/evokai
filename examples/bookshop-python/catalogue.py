from pydantic import BaseModel


class Book(BaseModel):
    id: str
    title: str
    author: str
    genre: str
    price: float
    stock: int


BOOKS: list[Book] = [
    Book(
        id="BK-101",
        title="The Lantern Keeper",
        author="Mara Ellison",
        genre="Fiction",
        price=16.5,
        stock=7,
    ),
    Book(
        id="BK-102",
        title="Salt and Paper",
        author="Owen Hartley",
        genre="Poetry",
        price=12.0,
        stock=3,
    ),
    Book(
        id="BK-103",
        title="A Short History of Maps",
        author="Ines Calder",
        genre="History",
        price=22.9,
        stock=5,
    ),
    Book(
        id="BK-104",
        title="The Quiet Orchard",
        author="Mara Ellison",
        genre="Fiction",
        price=14.0,
        stock=0,
    ),
    Book(
        id="BK-105",
        title="Bread at Dawn",
        author="Lucia Ferrand",
        genre="Cooking",
        price=27.5,
        stock=4,
    ),
    Book(
        id="BK-106", title="Small Stars", author="Theo Marsh", genre="Children", price=9.9, stock=12
    ),
    Book(
        id="BK-107",
        title="Letters from the Coast",
        author="Owen Hartley",
        genre="Poetry",
        price=13.5,
        stock=2,
    ),
    Book(
        id="BK-108",
        title="How Bridges Stand",
        author="Ravi Kalan",
        genre="Science",
        price=24.0,
        stock=6,
    ),
]


def search(query: str = "", in_stock_only: bool = False) -> list[dict]:
    """Search books whose title,author or genre contains every word of the query.

    if query is empty, return all books(or all books in stock).

    """
    words = [w.strip() for w in query.lower().split() if w.strip()]

    results = []
    for b in BOOKS:
        if in_stock_only and b.stock == 0:
            continue
        searchable_text = f"{b.title} {b.author} {b.genre}".lower()
        if all(w in searchable_text for w in words):
            results.append(b.model_dump())
    return results
