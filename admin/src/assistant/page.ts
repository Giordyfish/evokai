/**
 * The assistant sees and moves the console page with the person: its `read_page` client tool reads what the page
 * shows (figures and their names as written there), `show_on_page` scrolls to a text and highlights it, or scrolls
 * by a screen. The page is the `<main>` of the shell; the assistant's own panel is outside it.
 */
import { pageContext } from './launcher'

export const PAGE_TEXT_MAX = 6000
const SPOT_MS = 2400

export interface PageRead {
  path: string
  title: string
  /** The page's visible text, one line per block, cut at `PAGE_TEXT_MAX` characters. */
  text: string
}

export interface ShowArgs {
  text?: unknown
  scroll?: unknown
}

export interface Shown {
  /** The text the view moved to and highlighted, as the page writes it. */
  shown?: string
  scrolled?: 'up' | 'down' | 'top' | 'bottom'
  /** After the scroll: whether there is more of the page below, and above. */
  moreBelow?: boolean
  moreAbove?: boolean
}

/** Spaces collapsed and blank lines dropped, cut at `max` characters. */
export function cleanText(raw: string, max = PAGE_TEXT_MAX): string {
  const out = raw
    .split('\n')
    .map((l) => l.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n')
  return out.length > max ? `${out.slice(0, max)}\n[...]` : out
}

/** The form a text is compared in: lower case, spaces collapsed. */
export const norm = (s: string): string => s.replace(/\s+/g, ' ').trim().toLowerCase()

/**
 * Among the elements whose text holds the one asked for, the most precise: the shortest text (the innermost element).
 *
 * @returns Its index, or -1.
 */
export function pickTarget(texts: string[], wanted: string): number {
  const needle = norm(wanted)
  if (!needle) return -1
  let best = -1
  let len = Infinity
  texts.forEach((t, i) => {
    const n = norm(t)
    if (n.includes(needle) && n.length < len) {
      best = i
      len = n.length
    }
  })
  return best
}

const page = (): HTMLElement | null => document.querySelector('main')

/** The `read_page` tool: the page the person is looking at. */
export function readPage(): PageRead {
  const { path, title } = pageContext(location.hash, document.title)
  return { path, title, text: cleanText(page()?.innerText ?? '') }
}

function scroller(el: HTMLElement): HTMLElement {
  for (let n: HTMLElement | null = el; n; n = n.parentElement) {
    const y = getComputedStyle(n).overflowY
    if ((y === 'auto' || y === 'scroll') && n.scrollHeight > n.clientHeight) return n
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement
}

/** The box worth highlighting around a small match: a figure's name lights up with its value. */
function box(el: HTMLElement, root: HTMLElement): HTMLElement {
  const own = norm(el.innerText).length
  let at = el
  while (at.parentElement && at.parentElement !== root && norm(at.parentElement.innerText).length <= Math.max(80, own * 3)) {
    at = at.parentElement
  }
  return at
}

function spot(el: HTMLElement): void {
  el.classList.remove('assistant-spot')
  void el.offsetWidth
  el.classList.add('assistant-spot')
  setTimeout(() => el.classList.remove('assistant-spot'), SPOT_MS)
}

/** The `show_on_page` tool: move the view to a text, or by a screen. */
export function showOnPage(args: ShowArgs): Shown {
  const root = page()
  if (!root) throw new Error('No console page is open.')
  const text = typeof args.text === 'string' ? args.text.trim() : ''
  if (text) {
    // Elements of the page only (an SVG chart has no innerText), and only those on screen.
    const els = [...root.querySelectorAll('*')].filter((e): e is HTMLElement => e instanceof HTMLElement && e.getClientRects().length > 0)
    const i = pickTarget(
      els.map((e) => e.innerText),
      text,
    )
    const found = els[i]
    if (!found) throw new Error(`The page does not show "${text}". Read the page (read_page) for what it shows.`)
    const target = box(found, root)
    target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    spot(target)
    return { shown: cleanText(target.innerText, 200) }
  }
  const dir = args.scroll
  if (dir !== 'up' && dir !== 'down' && dir !== 'top' && dir !== 'bottom') throw new Error('Give text, or scroll: up, down, top or bottom.')
  const s = scroller(root)
  const max = s.scrollHeight - s.clientHeight
  const step = Math.round(s.clientHeight * 0.8)
  const to = dir === 'top' ? 0 : dir === 'bottom' ? max : Math.min(max, Math.max(0, s.scrollTop + (dir === 'down' ? step : -step)))
  s.scrollTo({ top: to, behavior: 'smooth' })
  return { scrolled: dir, moreBelow: to < max - 2, moreAbove: to > 2 }
}
