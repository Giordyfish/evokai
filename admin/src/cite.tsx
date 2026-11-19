/** Answers with citations: `[n]` markers of known knowledge passages as superscripts, and the sources under them. */
import type { ComponentChildren } from 'preact'

import type { Citation } from '@client/core/types'
import { t } from './i18n'

const MARK = /\[(\d{1,2})\](?!\()/g

/** The citation numbers a text uses, in order of first use. */
export function cited(text: string): number[] {
  const out: number[] = []
  for (const m of text.matchAll(MARK)) {
    const n = Number(m[1])
    if (!out.includes(n)) out.push(n)
  }
  return out
}

/** Where a passage is, in words: «p. 3 · Art. 2». */
export function where(c: Pick<Citation, 'location'>): string {
  const l = c.location
  const page = l.page != null ? t('kb.page', { page: l.pageEnd ? `${l.page}-${l.pageEnd}` : String(l.page) }) : ''
  return [page, l.section ?? ''].filter(Boolean).join(' · ')
}

/** One marker: the number in superscript, a link to the document when it has an address. */
export function Sup({ c }: { c: Citation }) {
  return (
    <sup class="cite">
      {c.url ? (
        <a href={c.url} target="_blank" rel="noopener noreferrer" title={c.title}>
          {c.index}
        </a>
      ) : (
        <span title={c.title}>{c.index}</span>
      )}
    </sup>
  )
}

/** A text with its markers drawn as superscripts (markers without a citation stay as they are). */
export function CitedText({ text, cites }: { text: string; cites: Citation[] }) {
  const byIndex = new Map(cites.map((c) => [c.index, c]))
  const out: ComponentChildren[] = []
  let last = 0
  for (const m of text.matchAll(MARK)) {
    const c = byIndex.get(Number(m[1]))
    if (!c) continue
    out.push(text.slice(last, m.index))
    out.push(<Sup key={m.index} c={c} />)
    last = m.index + m[0].length
  }
  out.push(text.slice(last))
  return <>{out}</>
}

/** The sources a text cites, in order of first use, with their place and the start of the passage. */
export function Sources({ text, cites, all, bare }: { text?: string; cites: Citation[]; all?: boolean; bare?: boolean }) {
  const byIndex = new Map(cites.map((c) => [c.index, c]))
  const list = all ? cites : cited(text ?? '').map((n) => byIndex.get(n)).filter((c): c is Citation => !!c)
  if (!list.length) return null
  return (
    <div class={bare ? 'sources bare' : 'sources'}>
      {bare ? null : <div class="sources-h">{t('kb.sources')}</div>}
      <ol>
        {list.map((c) => {
          const w = where(c)
          return (
            <li key={c.index}>
              <span class="src-n">{c.index}</span>
              <span class="src-b">
                {c.url ? (
                  <a class="src-t" href={c.url} target="_blank" rel="noopener noreferrer">
                    {c.title}
                  </a>
                ) : (
                  <span class="src-t">{c.title}</span>
                )}
                {w ? <span class="src-w">{w}</span> : null}
                {c.snippet ? <span class="src-s">{c.snippet}</span> : null}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
