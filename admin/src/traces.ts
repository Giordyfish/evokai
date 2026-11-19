/** Pure helpers of the Traces and Conversations pages: the waterfall's layout, tones, filters and deltas. */
import type { SpanKind, TraceQuery, TraceSpan, TraceSummary, VersionStats } from '@client/core/types'
import { has, t } from './i18n'
import type { Tone } from './ui'

/** A span placed on the waterfall: its depth under the root and its bar as shares of the whole trace. */
export interface Placed {
  span: TraceSpan
  depth: number
  left: number
  width: number
}

/** The spans in time order, each children after its parent, with its depth and its bar (percent of the trace). */
export function layout(spans: TraceSpan[]): Placed[] {
  if (!spans.length) return []
  const end = Math.max(1, ...spans.map((s) => s.start + s.ms))
  const ids = new Set(spans.map((s) => s.id))
  const children = new Map<string, TraceSpan[]>()
  const roots: TraceSpan[] = []
  for (const s of spans) {
    if (s.parent && ids.has(s.parent) && s.parent !== s.id) {
      const list = children.get(s.parent) ?? []
      list.push(s)
      children.set(s.parent, list)
    } else roots.push(s)
  }
  const byStart = (a: TraceSpan, b: TraceSpan) => a.start - b.start
  const out: Placed[] = []
  const seen = new Set<string>()
  const walk = (s: TraceSpan, depth: number) => {
    if (seen.has(s.id)) return
    seen.add(s.id)
    const left = Math.min(100, (s.start / end) * 100)
    out.push({ span: s, depth, left, width: Math.max(0.6, Math.min(100 - left, (s.ms / end) * 100)) })
    for (const c of (children.get(s.id) ?? []).sort(byStart)) walk(c, depth + 1)
  }
  for (const r of roots.sort(byStart)) walk(r, 0)
  return out
}

/** The colour token of a kind of span (the playground's event colours). */
export const SPAN_COLOR: Record<SpanKind, string> = {
  turn: 'var(--e-meta)',
  run: 'var(--e-meta)',
  eval: 'var(--e-meta)',
  model: 'var(--e-text)',
  tool: 'var(--e-tool)',
  guard: 'var(--e-confirm)',
  retrieval: 'var(--e-chart)',
  voice: 'var(--e-usage)',
  workflow: 'var(--e-meta)',
  step: 'var(--e-tool)',
}

export const spanColor = (s: TraceSpan): string => (s.status === 'error' ? 'var(--s-bad)' : SPAN_COLOR[s.kind] ?? 'var(--e-meta)')

/** The tone of a trace's outcome tag. */
export function outcomeTone(t: Pick<TraceSummary, 'status' | 'reason' | 'error'>): Tone {
  if (t.status === 'failed') return 'bad'
  if (t.status === 'blocked' || t.reason === 'guard' || t.reason === 'limit') return 'warn'
  if (t.status === 'interrupted') return ''
  if (t.reason === 'confirm' || t.reason === 'ask') return 'vi'
  return t.error ? 'warn' : 'ok'
}

/** The «slow» choices of the filter, in milliseconds. */
export const SLOW = [0, 3_000, 10_000, 30_000] as const

/** The filters the URL carries (`#/apps/x/traces?agent=a&guard=1`), as a query for the service. */
export function queryOf(q: URLSearchParams): TraceQuery {
  const out: TraceQuery = {}
  const text = (k: keyof TraceQuery) => {
    const v = q.get(k)
    if (v) (out as Record<string, unknown>)[k] = v
  }
  ;(['agent', 'version', 'subject', 'outcome', 'kind', 'feedback', 'from', 'to'] as const).forEach(text)
  if (q.get('guard') === '1') out.guard = true
  if (q.get('errors') === '1') out.errors = true
  const min = Number(q.get('minMs'))
  if (Number.isFinite(min) && min > 0) out.minMs = min
  return out
}

/** The query as URL parameters (empty and false values left out). */
export function paramsOf(t: TraceQuery): URLSearchParams {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(t)) {
    if (v === undefined || v === null || v === '' || v === false || v === 0) continue
    p.set(k, v === true ? '1' : v instanceof Date ? v.toISOString() : String(v))
  }
  return p
}

/** A value of version B against A: the difference and whether it is better (lower latency, fewer errors...). */
export function delta(a: number | null | undefined, b: number | null | undefined, higherIsBetter: boolean): { diff: number; better: boolean } | null {
  if (a == null || b == null) return null
  const diff = b - a
  return { diff, better: diff === 0 ? true : higherIsBetter ? diff > 0 : diff < 0 }
}

/** The metrics of the version comparison, in their order: key, whether higher is better, and how it reads. */
export const METRICS: Array<{ key: keyof VersionStats; higher: boolean; kind: 'count' | 'ms' | 'pct' }> = [
  { key: 'turns', higher: true, kind: 'count' },
  { key: 'medianMs', higher: false, kind: 'ms' },
  { key: 'p95Ms', higher: false, kind: 'ms' },
  { key: 'medianFirstTokenMs', higher: false, kind: 'ms' },
  { key: 'errorRate', higher: false, kind: 'pct' },
  { key: 'toolErrorRate', higher: false, kind: 'pct' },
  { key: 'guardRate', higher: false, kind: 'pct' },
  { key: 'feedbackRatio', higher: true, kind: 'pct' },
  { key: 'usageShare', higher: false, kind: 'pct' },
]

/** A content value of a span as text to show (JSON pretty-printed when it is a document). */
export function contentText(v: unknown): string {
  if (typeof v !== 'string') return JSON.stringify(v, null, 2)
  const s = v.trim()
  if ((s.startsWith('{') && s.endsWith('}')) || (s.startsWith('[') && s.endsWith(']'))) {
    try {
      return JSON.stringify(JSON.parse(s), null, 2)
    } catch {
      return v
    }
  }
  return v
}

/** The labels a reviewer can pick, in their order. */
export const LABELS = ['good', 'wrong_answer', 'wrong_tool', 'hallucination', 'unsafe', 'slow', 'needs_followup'] as const

/** The outcome of a trace as people read it (its reason, else its status; an unknown one as it is). */
export function outcomeLabel(x: Pick<TraceSummary, 'status' | 'reason'>): string {
  const key = `tr.out.${x.reason ?? x.status}`
  return has(key) ? t(key) : (x.reason ?? x.status)
}

/** A reviewer's label as people read it (a label the console does not know, as it is). */
export function labelText(label: string): string {
  const key = `cv.label.${label}`
  return has(key) ? t(key) : label
}
