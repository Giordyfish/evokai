/**
 * A small Markdown renderer for answers: headings, paragraphs, emphasis, inline code, code blocks, lists, quotes,
 * rules, links and GitHub tables. It builds elements, never HTML strings, so an answer cannot inject markup. Links keep
 * their text; only relative paths, http(s) and mailto targets become links.
 */
import type { ComponentChildren, VNode } from 'preact'
import type { Citation } from '@evokai/evokai-client/browser'

type Block =
  | { t: 'h'; level: number; text: string }
  | { t: 'p'; text: string }
  | { t: 'code'; text: string; lang: string }
  | { t: 'ul' | 'ol'; items: string[]; start: number }
  | { t: 'quote'; text: string }
  | { t: 'hr' }
  | { t: 'table'; head: string[]; align: Array<'left' | 'center' | 'right' | null>; rows: string[][] }

const LIST = /^\s{0,3}([-*+]|\d{1,9}[.)])\s+(.*)$/
const FENCE = /^\s{0,3}(```|~~~)\s*([\w-]*)\s*$/
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/
const TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/

const cells = (line: string): string[] =>
  line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split(/(?<!\\)\|/)
    .map((c) => c.trim().replace(/\\\|/g, '|'))

/** Split Markdown into blocks. */
export function blocks(md: string): Block[] {
  const lines = md.replace(/\r\n?/g, '\n').split('\n')
  const out: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i] as string
    if (!line.trim()) {
      i++
      continue
    }
    const fence = FENCE.exec(line)
    if (fence) {
      const body: string[] = []
      i++
      while (i < lines.length && !(lines[i] as string).trim().startsWith(fence[1] as string)) body.push(lines[i++] as string)
      i++
      out.push({ t: 'code', text: body.join('\n'), lang: fence[2] ?? '' })
      continue
    }
    const h = HEADING.exec(line)
    if (h) {
      out.push({ t: 'h', level: (h[1] as string).length, text: h[2] ?? '' })
      i++
      continue
    }
    if (RULE.test(line)) {
      out.push({ t: 'hr' })
      i++
      continue
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1] as string)) {
      const head = cells(line)
      const align = cells(lines[i + 1] as string).map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : c.startsWith(':') ? 'left' : null,
      )
      const rows: string[][] = []
      i += 2
      while (i < lines.length && (lines[i] as string).includes('|') && (lines[i] as string).trim()) rows.push(cells(lines[i++] as string))
      out.push({ t: 'table', head, align, rows })
      continue
    }
    if (/^\s{0,3}>/.test(line)) {
      const body: string[] = []
      while (i < lines.length && /^\s{0,3}>/.test(lines[i] as string)) body.push((lines[i++] as string).replace(/^\s{0,3}>\s?/, ''))
      out.push({ t: 'quote', text: body.join('\n') })
      continue
    }
    const li = LIST.exec(line)
    if (li) {
      const ordered = /\d/.test(li[1] as string)
      const items: string[] = []
      const start = ordered ? Number.parseInt(li[1] as string, 10) : 1
      while (i < lines.length) {
        const m = LIST.exec(lines[i] as string)
        if (m && /\d/.test(m[1] as string) === ordered) {
          items.push(m[2] ?? '')
          i++
        } else if (items.length && /^\s{2,}\S/.test(lines[i] as string)) {
          items[items.length - 1] += `\n${(lines[i++] as string).trim()}`
        } else break
      }
      out.push({ t: ordered ? 'ol' : 'ul', items, start })
      continue
    }
    const body: string[] = []
    while (
      i < lines.length &&
      (lines[i] as string).trim() &&
      !FENCE.test(lines[i] as string) &&
      !HEADING.test(lines[i] as string) &&
      !LIST.test(lines[i] as string) &&
      !/^\s{0,3}>/.test(lines[i] as string) &&
      !((lines[i] as string).includes('|') && TABLE_SEP.test((lines[i + 1] as string | undefined) ?? ''))
    )
      body.push(lines[i++] as string)
    out.push({ t: 'p', text: body.join('\n') })
  }
  return out
}

/** Whether a link target may be followed: a relative path, http(s) or mailto. */
export function safeHref(href: string): string | null {
  const h = href.trim()
  if (h.startsWith('/') && !h.startsWith('//')) return h
  if (/^https?:\/\//i.test(h) || /^mailto:/i.test(h)) return h
  if (h.startsWith('#')) return h
  return null
}

const INLINE = /\[(\d{1,2})\](?!\()|(`+)([\s\S]*?[^`])\2(?!`)|\*\*([\s\S]+?)\*\*|__([\s\S]+?)__|\*([^\s*][\s\S]*?)\*|(?<![\w])_([^\s_][\s\S]*?)_(?![\w])|~~([\s\S]+?)~~|\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|(https?:\/\/[^\s<>()]+[^\s<>().,;:!?'"])|\n/

/** Citation markers (`[n]`) an answer uses, in order of first use. */
export function citedIndexes(text: string): number[] {
  const out: number[] = []
  for (const m of text.matchAll(/\[(\d{1,2})\](?!\()/g)) {
    const n = Number(m[1])
    if (!out.includes(n)) out.push(n)
  }
  return out
}

/** How a citation marker is drawn: its number in superscript, a link to the source when it has one. */
export type CiteRenderer = (c: Citation, key: string) => ComponentChildren

/** Inline Markdown to elements. `cites` turns `[n]` markers of known citations into superscripts. */
export function inline(text: string, key = 'i', cites?: Map<number, Citation>, cite?: CiteRenderer): ComponentChildren[] {
  const out: ComponentChildren[] = []
  let rest = text
  let n = 0
  const sub = (t: string, k: string) => inline(t, k, cites, cite)
  while (rest) {
    const m = INLINE.exec(rest)
    if (!m) {
      out.push(rest)
      break
    }
    if (m.index) out.push(rest.slice(0, m.index))
    const k = `${key}${n++}`
    if (m[1] !== undefined) {
      const c = cites?.get(Number(m[1]))
      out.push(c && cite ? cite(c, k) : m[0])
    } else if (m[2]) out.push(<code key={k}>{m[3]}</code>)
    else if (m[4] !== undefined || m[5] !== undefined) out.push(<strong key={k}>{sub((m[4] ?? m[5]) as string, k)}</strong>)
    else if (m[6] !== undefined || m[7] !== undefined) out.push(<em key={k}>{sub((m[6] ?? m[7]) as string, k)}</em>)
    else if (m[8] !== undefined) out.push(<del key={k}>{sub(m[8], k)}</del>)
    else if (m[9] !== undefined) {
      const href = safeHref(m[10] ?? '')
      out.push(
        href ? (
          <a key={k} href={href} {...(/^https?:/i.test(href) ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
            {sub(m[9], k)}
          </a>
        ) : (
          <span key={k}>{sub(m[9], k)}</span>
        ),
      )
    } else if (m[11] !== undefined) {
      out.push(
        <a key={k} href={m[11]} target="_blank" rel="noopener noreferrer">
          {m[11]}
        </a>,
      )
    } else out.push(<br key={k} />)
    rest = rest.slice(m.index + m[0].length)
  }
  return out
}

/** A Markdown answer as elements. */
export function Markdown({ text, cites, cite }: { text: string; cites?: Map<number, Citation>; cite?: CiteRenderer }): VNode {
  const inline_ = (t: string, k: string) => inline(t, k, cites, cite)
  return (
    <div class="md" part="markdown">
      {blocks(text).map((b, i) => {
        const k = `b${i}`
        switch (b.t) {
          case 'h': {
            const H = `h${Math.min(6, b.level + 2)}` as 'h3'
            return <H key={k}>{inline_(b.text, k)}</H>
          }
          case 'p':
            return <p key={k}>{inline_(b.text, k)}</p>
          case 'code':
            return (
              <pre key={k}>
                <code data-lang={b.lang || undefined}>{b.text}</code>
              </pre>
            )
          case 'ul':
            return (
              <ul key={k}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline_(it, `${k}-${j}`)}</li>
                ))}
              </ul>
            )
          case 'ol':
            return (
              <ol key={k} start={b.start}>
                {b.items.map((it, j) => (
                  <li key={j}>{inline_(it, `${k}-${j}`)}</li>
                ))}
              </ol>
            )
          case 'quote':
            return <blockquote key={k}>{inline_(b.text, k)}</blockquote>
          case 'hr':
            return <hr key={k} />
          case 'table':
            return (
              <div key={k} class="tablewrap">
                <table>
                  <thead>
                    <tr>
                      {b.head.map((c, j) => (
                        <th key={j} style={b.align[j] ? { textAlign: b.align[j] as string } : undefined}>
                          {inline_(c, `${k}h${j}`)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, ri) => (
                      <tr key={ri}>
                        {b.head.map((_, j) => (
                          <td key={j} style={b.align[j] ? { textAlign: b.align[j] as string } : undefined}>
                            {inline_(r[j] ?? '', `${k}r${ri}c${j}`)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
        }
      })}
    </div>
  )
}
