// The shop's catalogue: the page lists it and the MCP tool searches it. A real shop reads its own database here.

export const books = [
  { id: 'BK-101', title: 'The Lantern Keeper', author: 'Mara Ellison', genre: 'Fiction', price: 16.5, stock: 7 },
  { id: 'BK-102', title: 'Salt and Paper', author: 'Owen Hartley', genre: 'Poetry', price: 12.0, stock: 3 },
  { id: 'BK-103', title: 'A Short History of Maps', author: 'Ines Calder', genre: 'History', price: 22.9, stock: 5 },
  { id: 'BK-104', title: 'The Quiet Orchard', author: 'Mara Ellison', genre: 'Fiction', price: 14.0, stock: 0 },
  { id: 'BK-105', title: 'Bread at Dawn', author: 'Lucia Ferrand', genre: 'Cooking', price: 27.5, stock: 4 },
  { id: 'BK-106', title: 'Small Stars', author: 'Theo Marsh', genre: 'Children', price: 9.9, stock: 12 },
  { id: 'BK-107', title: 'Letters from the Coast', author: 'Owen Hartley', genre: 'Poetry', price: 13.5, stock: 2 },
  { id: 'BK-108', title: 'How Bridges Stand', author: 'Ravi Kalan', genre: 'Science', price: 24.0, stock: 6 },
]

/** Books whose title, author or genre contains every word of the query (all books for an empty query). */
export function search(query = '', { inStockOnly = false } = {}) {
  const words = String(query).toLowerCase().split(/\s+/).filter(Boolean)
  return books.filter((b) => {
    if (inStockOnly && b.stock === 0) return false
    const text = `${b.title} ${b.author} ${b.genre}`.toLowerCase()
    return words.every((w) => text.includes(w))
  })
}
