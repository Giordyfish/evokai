/**
 * Charts, schemas, questions and confirmations drawn by the agent (the `chart`, `diagram`, `ask` and `confirm` events),
 * sized for a ~25rem panel. Plain SVG and CSS: thin marks, direct labels instead of axes, one highlighted mark, no
 * gridlines. Colours come from the theme's series; text always wears text colours, never a series colour. The payloads
 * are the service's (`Chart`, `Diagram`).
 */
import type { ComponentChildren, VNode } from 'preact'
import { useCallback, useEffect, useState } from 'preact/hooks'

import { confirmReasonText, localePack } from '@evokai/evokai-client/browser'
import type { Chart, ConfirmReason, Diagram, KpiChart, Point, ShareChart, Unit, XYChart } from '@evokai/evokai-client/browser'
import { useUi } from './context'

let uid = 0
const SERIES = ['var(--ek-s1)', 'var(--ek-s2)', 'var(--ek-s3)', 'var(--ek-s4)', 'var(--ek-s5)']

function useFmt() {
  const { fmt, lang } = useUi()
  return (v: number, unit: Unit) => fmt(v, unit, lang)
}

function Frame({ title, subtitle, label, children }: { title: string; subtitle?: string; label: string; children: ComponentChildren }) {
  return (
    <figure class="viz" part="chart">
      {title || subtitle ? (
        <figcaption>
          <span class="vt">{title}</span>
          {subtitle ? <span class="vs">{subtitle}</span> : null}
        </figcaption>
      ) : null}
      <div role="img" aria-label={label}>
        {children}
      </div>
    </figure>
  )
}

function Legend({ items }: { items: Array<{ name: string; color: string; dashed?: boolean }> }) {
  return (
    <div class="legend" aria-hidden="true">
      {items.map((it) => (
        <span key={it.name}>
          <i style={{ background: it.dashed ? `repeating-linear-gradient(90deg, ${it.color} 0 3px, transparent 3px 5px)` : it.color }} />
          {it.name}
        </span>
      ))}
    </div>
  )
}

function Bars({ c }: { c: XYChart }) {
  const f = useFmt()
  const max = Math.max(1e-9, ...c.points.map((p) => Math.abs(p.value)))
  const summary = c.points.map((p) => `${p.label} ${f(p.value, c.unit)}`).join(', ')
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={summary}>
      <ul class="bars">
        {c.points.map((p) => {
          const hi = c.highlight === p.label
          return (
            <li key={p.label} title={`${p.label}: ${f(p.value, c.unit)}`} class={hi ? 'hi' : ''}>
              <span class="bl">{p.label}</span>
              <span class="bt" aria-hidden="true">
                <span style={{ width: `${Math.max(2, (Math.abs(p.value) / max) * 100)}%`, background: p.value < 0 ? 'var(--ek-ghost)' : hi ? 'var(--ek-hi)' : 'var(--ek-s1)' }} />
              </span>
              <span class="num">{f(p.value, c.unit)}</span>
            </li>
          )
        })}
      </ul>
    </Frame>
  )
}

/** A column with only its top corners rounded, anchored to the baseline. */
function column(x: number, y0: number, y1: number, w: number) {
  const top = Math.min(y0, y1)
  const h = Math.abs(y1 - y0)
  const r = Math.min(4, w / 2, h)
  if (y1 <= y0) return `M${x},${top + h} V${top + r} Q${x},${top} ${x + r},${top} H${x + w - r} Q${x + w},${top} ${x + w},${top + r} V${top + h} Z`
  return `M${x},${top} V${top + h - r} Q${x},${top + h} ${x + r},${top + h} H${x + w - r} Q${x + w},${top + h} ${x + w},${top + h - r} V${top} Z`
}

const short = (label: string) => (label.length > 10 ? `${label.slice(0, 9)}…` : label)

function Columns({ c }: { c: XYChart }) {
  const f = useFmt()
  const { s } = useUi()
  const W = 336
  const H = 150
  const top = 16
  const base = H - 22
  const n = c.points.length
  const all = [...c.points, ...(c.compare?.points ?? [])].map((p) => p.value)
  const lo = Math.min(0, ...all)
  const hi = Math.max(0, ...all)
  const span = hi - lo || 1
  const y = (v: number) => top + ((hi - v) / span) * (base - top)
  const slot = W / n
  const pair = !!c.compare
  const w = Math.min(pair ? 14 : 28, slot * (pair ? 0.34 : 0.58))
  const every = Math.ceil(n / 8)
  const cmp = (label: string) => c.compare?.points.find((p) => p.label === label)
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={c.points.map((p) => `${p.label} ${f(p.value, c.unit)}`).join(', ')}>
      <svg viewBox={`0 0 ${W} ${H}`} class="chart">
        <line x1="0" x2={W} y1={y(0)} y2={y(0)} stroke="var(--ek-border)" stroke-width="1" />
        {c.points.map((p, i) => {
          const cx = slot * i + slot / 2
          const mark = c.highlight ? c.highlight === p.label : i === n - 1
          const q = cmp(p.label)
          const x = pair ? cx + 1 : cx - w / 2
          return (
            <g key={p.label}>
              <title>{`${p.label}: ${f(p.value, c.unit)}${q ? `, ${c.compare?.name ?? ""} ${f(q.value, c.unit)}` : ''}`}</title>
              <rect x={slot * i} y={0} width={slot} height={H} fill="transparent" />
              {q ? <path d={column(cx - w - 1, y(0), y(q.value), w)} fill="var(--ek-ghost)" /> : null}
              <path d={column(x, y(0), y(p.value), w)} fill={mark ? 'var(--ek-hi)' : 'var(--ek-s1)'} />
              {n <= 8 ? (
                <text x={pair ? x + w / 2 : cx} y={Math.min(y(p.value), y(0)) - 5} text-anchor="middle" font-size="9.5" class={mark ? 'tx strong' : 'tx'}>
                  {f(p.value, c.unit)}
                </text>
              ) : null}
              {i % every === 0 || i === n - 1 ? (
                <text x={cx} y={H - 6} text-anchor="middle" font-size="10" class="tx">
                  {short(p.label)}
                </text>
              ) : null}
            </g>
          )
        })}
      </svg>
      {pair ? <Legend items={[{ name: c.name || s.current, color: 'var(--ek-s1)' }, { name: c.compare?.name ?? "", color: 'var(--ek-ghost)' }]} /> : null}
    </Frame>
  )
}

function Lines({ c }: { c: XYChart }) {
  const f = useFmt()
  const { s } = useUi()
  const [id] = useState(() => `ekg${++uid}`)
  const W = 336
  const H = 140
  const padX = 12
  const top = 18
  const base = H - 24
  const n = c.points.length
  const all = [...c.points, ...(c.compare?.points ?? [])].map((p) => p.value)
  let lo = Math.min(...all)
  let hi = Math.max(...all)
  if (c.type === 'area') lo = Math.min(0, lo)
  const span = hi - lo || Math.abs(hi) || 1
  if (c.type === 'line') {
    lo -= span * 0.12
    hi += span * 0.08
  }
  const x = (i: number) => (n === 1 ? W / 2 : padX + (i * (W - 2 * padX)) / (n - 1))
  const yv = (v: number) => top + ((hi - v) / (hi - lo || 1)) * (base - top)
  const path = (ps: Point[]) =>
    ps
      .map((p, i) => `${i ? 'L' : 'M'}${x(Math.max(0, c.points.findIndex((q) => q.label === p.label))).toFixed(1)},${yv(p.value).toFixed(1)}`)
      .join(' ')
  const d = path(c.points)
  const focus = c.highlight ? c.points.findIndex((p) => p.label === c.highlight) : n - 1
  const maxI = c.points.reduce((m, p, i) => (p.value > (c.points[m] as Point).value ? i : m), 0)
  const labelled = new Set(n <= 6 ? c.points.map((_, i) => i) : [0, focus, maxI])
  const every = Math.ceil(n / 7)
  const anchor = (i: number) => (i === 0 && n > 1 ? 'start' : i === n - 1 && n > 1 ? 'end' : 'middle')
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={c.points.map((p) => `${p.label} ${f(p.value, c.unit)}`).join(', ')}>
      <svg viewBox={`0 0 ${W} ${H}`} class="chart">
        <defs>
          <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0" stop-color="var(--ek-s1)" stop-opacity="0.24" />
            <stop offset="1" stop-color="var(--ek-s1)" stop-opacity="0" />
          </linearGradient>
        </defs>
        {c.type === 'area' && n > 1 ? <path d={`${d} L${x(n - 1)},${yv(Math.max(lo, 0))} L${x(0)},${yv(Math.max(lo, 0))} Z`} fill={`url(#${id})`} /> : null}
        {c.compare ? <path d={path(c.compare.points)} fill="none" stroke="var(--ek-ghost)" stroke-width="1.5" stroke-dasharray="3 3" stroke-linecap="round" stroke-linejoin="round" /> : null}
        <path d={d} fill="none" stroke="var(--ek-s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        {c.points.map((p, i) => (
          <g key={p.label}>
            <title>{`${p.label}: ${f(p.value, c.unit)}`}</title>
            <circle cx={x(i)} cy={yv(p.value)} r="10" fill="transparent" />
            {i === focus ? (
              <circle cx={x(i)} cy={yv(p.value)} r="4.5" fill="var(--ek-hi)" stroke="var(--ek-surface)" stroke-width="2" />
            ) : n <= 12 ? (
              <circle cx={x(i)} cy={yv(p.value)} r="2" fill="var(--ek-s1)" />
            ) : null}
            {labelled.has(i) ? (
              <text x={x(i)} y={yv(p.value) - 9} text-anchor={anchor(i)} font-size="9.5" class={i === focus ? 'tx strong' : 'tx'}>
                {f(p.value, c.unit)}
              </text>
            ) : null}
            {i % every === 0 || i === n - 1 ? (
              <text x={x(i)} y={H - 6} text-anchor={anchor(i)} font-size="10" class="tx">
                {short(p.label)}
              </text>
            ) : null}
          </g>
        ))}
      </svg>
      {c.compare ? <Legend items={[{ name: c.name || s.current, color: 'var(--ek-s1)' }, { name: c.compare.name, color: 'var(--ek-ghost)', dashed: true }]} /> : null}
    </Frame>
  )
}

function Shares({ c }: { c: ShareChart }) {
  const f = useFmt()
  const { s, lang } = useUi()
  const total = c.points.reduce((t, p) => t + p.value, 0) || 1
  const pct = (v: number) => `${new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format((v / total) * 100)}%`
  const color = (label: string, i: number) => (label === s.chartOther || label === 'Other' || label === 'Altro' ? 'var(--ek-rest)' : SERIES[i % SERIES.length])
  const legend = (
    <ul class="shares">
      {c.points.map((p, i) => (
        <li key={p.label} title={`${p.label}: ${f(p.value, c.unit)} (${pct(p.value)})`}>
          <i style={{ background: color(p.label, i) }} aria-hidden="true" />
          <span class="sl">{p.label}</span>
          <span class="num sub">{pct(p.value)}</span>
          <span class="num sv">{f(p.value, c.unit)}</span>
        </li>
      ))}
    </ul>
  )
  const label = `${c.points.map((p) => `${p.label} ${f(p.value, c.unit)}`).join(', ')}; ${s.total} ${f(total, c.unit)}`
  if (c.type === 'stacked')
    return (
      <Frame title={c.title} subtitle={c.subtitle} label={label}>
        <div class="stack" aria-hidden="true">
          {c.points.map((p, i) => (
            <span key={p.label} style={{ flexGrow: p.value, flexBasis: 0, background: color(p.label, i) }} />
          ))}
        </div>
        {legend}
      </Frame>
    )
  const R = 44
  const S = 12
  const C = 2 * Math.PI * R
  const gap = c.points.length > 1 ? 2.5 : 0
  let acc = 0
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={label}>
      <div class="donut">
        <svg viewBox="0 0 112 112" aria-hidden="true">
          <g transform="rotate(-90 56 56)">
            {c.points.map((p, i) => {
              const len = (p.value / total) * C
              const seg = (
                <circle key={p.label} cx="56" cy="56" r={R} fill="none" stroke={color(p.label, i)} stroke-width={S} stroke-dasharray={`${Math.max(0.5, len - gap)} ${C}`} stroke-dashoffset={-acc}>
                  <title>{`${p.label}: ${f(p.value, c.unit)} (${pct(p.value)})`}</title>
                </circle>
              )
              acc += len
              return seg
            })}
          </g>
          <text x="56" y="55" text-anchor="middle" font-size="13" class="tx strong">
            {f(total, c.unit)}
          </text>
          <text x="56" y="70" text-anchor="middle" font-size="9" class="tx">
            {s.total}
          </text>
        </svg>
        {legend}
      </div>
    </Frame>
  )
}

function Kpis({ c }: { c: KpiChart }) {
  const f = useFmt()
  const { lang } = useUi()
  const d1 = (v: number) => new Intl.NumberFormat(lang, { maximumFractionDigits: 1 }).format(v)
  const label = c.items.map((k) => `${k.label} ${f(k.value, k.unit)}${k.delta !== null ? `, ${k.delta > 0 ? '+' : ''}${d1(k.delta)}% ${k.note}` : ''}`).join('; ')
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={label}>
      <dl class={`kpis ${c.items.length > 1 ? 'two' : 'one'}`}>
        {c.items.map((k, i) => (
          <div key={`${k.label}${i}`}>
            <dt>{k.label}</dt>
            <dd class="kv num">{f(k.value, k.unit)}</dd>
            {k.delta !== null || k.note ? (
              <dd class="kn">
                {k.delta !== null ? (
                  <span class={`num ${k.delta < 0 ? 'down' : k.delta > 0 ? 'up' : ''}`}>
                    {k.delta > 0 ? '↑ +' : k.delta < 0 ? '↓ ' : ''}
                    {d1(k.delta)}%
                  </span>
                ) : null}
                {k.note ? <span>{k.delta !== null ? ' ' : ''}{k.note}</span> : null}
              </dd>
            ) : null}
          </div>
        ))}
      </dl>
    </Frame>
  )
}

function Picture({ c }: { c: Extract<Chart, { type: 'image' }> }) {
  // Only PNG and JPEG reach the client, as base64 the service checked; never an SVG or an address.
  const media = c.mediaType === 'image/jpeg' ? 'image/jpeg' : 'image/png'
  const safe = /^[A-Za-z0-9+/=]+$/.test(c.data) ? c.data : ''
  return (
    <Frame title={c.title} subtitle={c.subtitle} label={c.title}>
      {safe ? <img class="picture" src={`data:${media};base64,${safe}`} alt="" /> : null}
    </Frame>
  )
}

export function ChartView({ chart }: { chart: Chart }): VNode {
  switch (chart.type) {
    case 'image':
      return <Picture c={chart} />
    case 'kpi':
      return <Kpis c={chart} />
    case 'donut':
    case 'stacked':
      return <Shares c={chart} />
    case 'line':
    case 'area':
      return <Lines c={chart} />
    case 'column':
      return <Columns c={chart} />
    default:
      return <Bars c={chart} />
  }
}

function Steps({ d }: { d: Extract<Diagram, { kind: 'steps' }> }) {
  const { s } = useUi()
  const word = { done: s.stepDone, current: s.stepCurrent, todo: s.stepTodo }
  return (
    <Frame title={d.title} label={d.steps.map((x, i) => `${i + 1}. ${x.label} (${word[x.state]})`).join(', ')}>
      <ol class="steps">
        {d.steps.map((x, i) => (
          <li key={`${x.label}${i}`} data-state={x.state}>
            <span class="dot" aria-hidden="true">
              {x.state === 'done' ? (
                <svg viewBox="0 0 12 12">
                  <path d="M2.5 6.2l2.2 2.2 4.8-4.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
              ) : x.state === 'current' ? (
                <i />
              ) : null}
            </span>
            <span class="st">
              <span class="sl">{x.label}</span>
              {x.note ? <span class="sn">{x.note}</span> : null}
            </span>
          </li>
        ))}
      </ol>
    </Frame>
  )
}

function Tree({ d }: { d: Extract<Diagram, { kind: 'tree' }> }) {
  const note = (x: { label: string; note: string }) => `${x.label}${x.note ? ` ${x.note}` : ''}`
  return (
    <Frame title={d.title} label={`${note(d.root)}: ${d.children.map(note).join(', ')}`}>
      <div class="troot">
        <span class="chip">{d.root.label}</span>
        {d.root.note ? <span class="num strong">{d.root.note}</span> : null}
      </div>
      <ul class="tree">
        {d.children.map((c, i) => (
          <li key={`${c.label}${i}`}>
            <span class="tl">{c.label}</span>
            {c.note ? <span class="num sub">{c.note}</span> : null}
          </li>
        ))}
      </ul>
    </Frame>
  )
}

function Compare({ d }: { d: Extract<Diagram, { kind: 'compare' }> }) {
  const { s } = useUi()
  const diff = d.rows.filter((r) => r.differs).length
  return (
    <Frame
      title={d.title}
      subtitle={diff ? s.differences(diff) : s.same}
      label={d.rows.map((r) => `${r.label}: ${d.columns[0]} ${r.left || '-'}, ${d.columns[1]} ${r.right || '-'}${r.differs ? ` (${s.differs})` : ''}`).join('; ')}
    >
      <table class="compare">
        <thead>
          <tr>
            <th />
            <th>{d.columns[0]}</th>
            <th>{d.columns[1]}</th>
          </tr>
        </thead>
        <tbody>
          {d.rows.map((r, i) => (
            <tr key={`${r.label}${i}`} class={r.differs ? 'diff' : ''}>
              <td title={r.label}>
                {r.differs ? <i aria-hidden="true" /> : null}
                {r.label}
              </td>
              <td title={r.left}>{r.left || '-'}</td>
              <td title={r.right}>{r.right || '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Frame>
  )
}

export function DiagramView({ diagram }: { diagram: Diagram }): VNode {
  if (diagram.kind === 'steps') return <Steps d={diagram} />
  if (diagram.kind === 'tree') return <Tree d={diagram} />
  return <Compare d={diagram} />
}

/**
 * A question with tappable answers. While it is the open question, keys 1 to 5 pick an option when focus is not in a
 * field (or the message box is still empty).
 */
export function AskCard(p: {
  question: string
  options: string[]
  multiple: boolean
  other: boolean
  answered: string[] | null
  active: boolean
  disabled: boolean
  onAnswer: (labels: string[]) => void
  keyTarget?: EventTarget | null
}): VNode {
  const { s } = useUi()
  const [sel, setSel] = useState<string[]>([])
  const [free, setFree] = useState<string | null>(null)
  const done = p.answered !== null
  const { multiple, onAnswer, disabled } = p
  const pick = useCallback(
    (o: string) => {
      if (done || disabled) return
      if (!multiple) return onAnswer([o])
      setSel((x) => (x.includes(o) ? x.filter((y) => y !== o) : [...x, o]))
    },
    [done, disabled, multiple, onAnswer],
  )
  useEffect(() => {
    if (!p.active || done) return
    const target = p.keyTarget ?? (typeof window !== 'undefined' ? window : null)
    if (!target) return
    const onKey = (ev: Event) => {
      const e = ev as KeyboardEvent
      const t = (e.composedPath?.()[0] ?? e.target) as HTMLElement | null
      const field = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)
      const emptyBox = t?.tagName === 'TEXTAREA' && t.dataset.evokai === 'composer' && !(t as HTMLTextAreaElement).value
      if (e.ctrlKey || e.metaKey || e.altKey || (field && !emptyBox)) return
      const i = Number(e.key) - 1
      if (Number.isInteger(i) && i >= 0 && i < p.options.length) {
        e.preventDefault()
        pick(p.options[i] as string)
      }
    }
    target.addEventListener('keydown', onKey)
    return () => target.removeEventListener('keydown', onKey)
  }, [p.active, done, p.options, pick, p.keyTarget])
  const extra = done ? (p.answered as string[]).filter((a) => !p.options.includes(a)) : []
  return (
    <div class="card ask" part="card ask-card">
      <p class="q">{p.question}</p>
      <div role="group" aria-label={p.question} class="chips">
        {p.options.map((o, i) => {
          const chosen = done ? (p.answered as string[]).includes(o) : sel.includes(o)
          return (
            <button key={o} type="button" class={`chip ${chosen ? 'on' : ''} ${done && !chosen ? 'faded' : ''}`} aria-pressed={chosen} disabled={done || p.disabled} onClick={() => pick(o)}>
              {!done ? <kbd>{i + 1}</kbd> : null}
              {o}
            </button>
          )
        })}
        {extra.map((x) => (
          <span key={x} class="chip on">
            {x}
          </span>
        ))}
        {p.other && !done ? (
          <button type="button" class="chip sub" disabled={p.disabled} aria-expanded={free !== null} onClick={() => setFree((f) => (f === null ? '' : null))}>
            {s.other}
          </button>
        ) : null}
      </div>
      {free !== null && !done ? (
        <form
          class="free"
          onSubmit={(e) => {
            e.preventDefault()
            if (free.trim()) p.onAnswer([free.trim()])
          }}
        >
          <input autoFocus value={free} onInput={(e) => setFree(e.currentTarget.value)} aria-label={s.yourAnswer} placeholder={s.writeAnswer} />
          <button type="submit" class="chip on" disabled={!free.trim() || p.disabled}>
            {s.submit}
          </button>
        </form>
      ) : null}
      {p.multiple && !done ? (
        <div class="end">
          <button type="button" class="chip on strong" disabled={!sel.length || p.disabled} onClick={() => p.onAnswer(sel)}>
            {s.confirmChoice}
            {sel.length ? ` (${sel.length})` : ''}
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** The card for an action that changes data: what changes, then confirm or cancel. */
export function ConfirmCard(p: {
  items: string[]
  why?: ConfirmReason[] | undefined
  state: 'open' | 'approved' | 'rejected' | 'closed'
  disabled: boolean
  onDecide: (approve: boolean) => void
}): VNode {
  const { s, lang } = useUi()
  // Why an item waits: a rule's label, else the client's sentence for its key; a change needs no reason.
  const W = 'confirm.why.writes'
  const why = (i: number): string => {
    const w = p.why?.[i]
    return !w || w.key === W ? '' : confirmReasonText(w, localePack(lang))
  }
  const asks = !!p.why?.length && p.why.every((w) => w.key !== W)
  const title = p.state === 'open' ? (asks ? s.confirmAskTitle : s.confirmTitle) : p.state === 'approved' ? s.confirmed : p.state === 'rejected' ? s.cancelled : s.closed
  return (
    <div class="card confirm" part="card confirm-card" data-state={p.state} role={p.state === 'open' ? 'alertdialog' : undefined} aria-label={title}>
      <div class="ct">
        <i aria-hidden="true" />
        {title}
      </div>
      <ul>
        {p.items.map((it, i) => (
          <li key={it}>
            {it}
            {why(i) && <small part="confirm-reason">{why(i)}</small>}
          </li>
        ))}
      </ul>
      {p.state === 'open' ? (
        <div class="end">
          <button type="button" class="chip" disabled={p.disabled} onClick={() => p.onDecide(false)}>
            {s.cancel}
          </button>
          <button type="button" class="chip on strong" disabled={p.disabled} onClick={() => p.onDecide(true)}>
            {s.confirm}
          </button>
        </div>
      ) : null}
    </div>
  )
}
