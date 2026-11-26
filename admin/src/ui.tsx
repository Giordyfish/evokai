/**
 * Small presentational components. Pages compose these and never style by themselves, so the look lives in
 * styles.css (its tokens) and here.
 */
import type { ComponentChildren, TargetedMouseEvent } from 'preact'
import { useEffect, useLayoutEffect, useRef } from 'preact/hooks'

import { appColor } from './format'
import { t } from './i18n'
import { Fx, Ring } from './magic'

export const ICONS = {
  apps: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2.5" y="2.5" width="6" height="6" rx="1.5"/><rect x="11.5" y="2.5" width="6" height="6" rx="3"/><rect x="2.5" y="11.5" width="6" height="6" rx="3"/><rect x="11.5" y="11.5" width="6" height="6" rx="1.5"/></svg>',
  play: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 4.5A1.5 1.5 0 0 1 4.5 3h11A1.5 1.5 0 0 1 17 4.5v8a1.5 1.5 0 0 1-1.5 1.5H8l-4 3v-3H4.5"/><path d="M7 8.5h6"/></svg>',
  usage: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 16.5h14M5.5 13V9M10 13V5M14.5 13v-6"/></svg>',
  health: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><path d="M5 10h2.5l1.5-3 2 6 1.5-3H15"/></svg>',
  agents: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 15c2-1 3.5-5 7-11 3.5 6 5 10 7 11z"/><path d="M7.5 11.5h5"/></svg>',
  perms: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="14" height="5" rx="2.5"/><circle cx="14.5" cy="6.5" r="1.2" fill="currentColor"/><rect x="3" y="11" width="14" height="5" rx="2.5"/><circle cx="5.5" cy="13.5" r="1.2"/></svg>',
  mcp: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="5" cy="10" r="2.2"/><circle cx="15" cy="5" r="2.2"/><circle cx="15" cy="15" r="2.2"/><path d="M7 9l6-3M7 11l6 3"/></svg>',
  skills: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 3.5h8.5L16 7v9.5H4z"/><path d="M12.5 3.5V7H16M7 11h6M7 14h4"/></svg>',
  prompts: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3.5" width="14" height="13" rx="2"/><path d="M6.5 8l2.5 2-2.5 2M10.5 12.5h3"/></svg>',
  knowledge: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3.5 4.5c2.2-.9 4.4-.9 6.5.6v11c-2.1-1.5-4.3-1.5-6.5-.6z"/><path d="M16.5 4.5c-2.2-.9-4.4-.9-6.5.6v11c2.1-1.5 4.3-1.5 6.5-.6z"/></svg>',
  keys: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="7" cy="12" r="3.5"/><path d="M9.5 9.5L16 3M13.5 5.5l2 2"/></svg>',
  limits: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 14a7 7 0 1 1 14 0"/><path d="M10 14l3.5-4"/></svg>',
  security: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 2.5l6 2.5v4.5c0 4-2.6 6.8-6 8-3.4-1.2-6-4-6-8V5z"/><path d="M7.5 10l2 2 3.5-4"/></svg>',
  privacy: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="4" y="9" width="12" height="8.5" rx="2"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/><circle cx="10" cy="13" r="1.2" fill="currentColor"/></svg>',
  evals: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M8 2.5h4M9 2.5v5L4.5 15a1.6 1.6 0 0 0 1.4 2.5h8.2a1.6 1.6 0 0 0 1.4-2.5L11 7.5v-5"/><path d="M6.5 12.5h7"/></svg>',
  traces: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 4.5h9M5.5 8.5h8M8 12.5h6.5M10 16h7"/></svg>',
  conversations: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H8l-3 2.5V11a2 2 0 0 1-2-2z"/><path d="M16 7.5a1.5 1.5 0 0 1 1 1.4v3.6a1.5 1.5 0 0 1-1.5 1.5H15V16l-2.6-2H10"/></svg>',
  compliance: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 2.5l6 2.5v4.5c0 3.8-2.6 6.6-6 8-3.4-1.4-6-4.2-6-8V5z"/><path d="M7.2 10.2l2 2 3.8-4"/></svg>',
  audit: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M5 3.5h10v13H5z"/><path d="M7.5 7h5M7.5 10h5M7.5 13h3"/></svg>',
  workflows: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="2.5" width="6" height="4" rx="1"/><rect x="11" y="13.5" width="6" height="4" rx="1"/><path d="M6 6.5v4.5h8v2.5"/></svg>',
  approvals: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3.5" y="3" width="13" height="14" rx="2"/><path d="M7 10.5l2 2 4-4.5"/></svg>',
  access: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="7" r="3"/><path d="M4 17c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5"/></svg>',
  connections: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-.9.9M11.5 8.5a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l.9-.9"/></svg>',
  channels: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M3 5.5h9v6H7l-3 2.5v-2.5H3z"/><path d="M12 8h5v6h-1v2.5L13 14h-3v-2.5"/></svg>',
  providers: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2.2 3 4.5 3 7s-1 4.8-3 7c-2-2.2-3-4.5-3-7s1-4.8 3-7z"/></svg>',
  overview: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><circle cx="10" cy="10" r="2.5"/></svg>',
  chev: '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4.5 2.5L8 6l-3.5 3.5"/></svg>',
  undo: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5.5 3L2.5 6l3 3"/><path d="M2.5 6H10a3.5 3.5 0 0 1 0 7H7"/></svg>',
  sun: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="3.5"/><path d="M10 2v2M10 16v2M2 10h2M16 10h2M4.3 4.3l1.4 1.4M14.3 14.3l1.4 1.4M4.3 15.7l1.4-1.4M14.3 5.7l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M16 12.5A6.5 6.5 0 0 1 7.5 4a6.5 6.5 0 1 0 8.5 8.5z"/></svg>',
  menu: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 6h14M3 10h14M3 14h9"/></svg>',
  search: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" width="16" height="16"><circle cx="9" cy="9" r="5.5"/><path d="M13 13l4 4"/></svg>',
  check: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4.5 10.5l3.5 3.5 7.5-8"/></svg>',
  guide: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7"/><path d="M12.8 7.2l-1.7 3.9-3.9 1.7 1.7-3.9z" stroke-linejoin="round"/></svg>',
  plus: '<svg viewBox="0 0 64 64" fill="none" stroke="currentColor"><circle cx="32" cy="32" r="28" stroke-dasharray="2 4"/><circle cx="32" cy="32" r="18"/><path d="M32 24v16M24 32h16" stroke-width="1.6" stroke-linecap="round"/></svg>',
} as const
export type IconName = keyof typeof ICONS

/** An inline SVG icon (the wrapper does not take part in layout). */
export function Icon({ name, style }: { name: IconName; style?: string }) {
  const html = style ? ICONS[name].replace('<svg', `<svg style="${style}"`) : ICONS[name]
  return <i class="ico" dangerouslySetInnerHTML={{ __html: html }} />
}

type BtnProps = {
  children: ComponentChildren
  kind?: string
  sm?: boolean
  onClick?: (e: TargetedMouseEvent<HTMLButtonElement>) => void
  href?: string
  busy?: ComponentChildren | false
  /** The dark flame burns in the button (a turn running). */
  flame?: boolean
  disabled?: boolean
  check?: boolean
  type?: 'button' | 'submit'
  label?: string
  style?: string
  /** The effect that starts on pointerdown: `save` (also given by `check`), `confirm`, `key`. */
  fx?: 'save' | 'confirm' | 'key'
  /** The place a guided tour points at (`data-tour`, see src/tours). */
  tour?: string
}

/** A button; `busy` shows the drawing ring and its label, `check` lets a save draw its check mark. */
export function Btn(p: BtnProps) {
  const working = p.busy !== undefined && p.busy !== false
  const inner = working ? (
    <span class="lb">
      {p.flame ? (Fx({ k: 'flameLoop', size: 30, cls: 'btnflame' }) ?? <Ring />) : <Ring />}
      {p.busy}
    </span>
  ) : (
    <span class="lb">{p.children}</span>
  )
  const cls = `btn ${p.kind ?? ''} ${p.sm ? 'sm' : ''}`
  if (p.href)
    return (
      <a class={cls} href={p.href} aria-label={p.label} style={p.style} data-tour={p.tour}>
        {inner}
      </a>
    )
  return (
    <button
      class={cls}
      type={p.type ?? 'button'}
      onClick={p.onClick}
      disabled={p.disabled || working}
      aria-label={p.label}
      style={p.style}
      data-fx={p.fx ?? (p.check ? 'save' : undefined)}
      data-tour={p.tour}
    >
      {inner}
      {p.check ? (
        <span class="ok">
          <Icon name="check" />
        </span>
      ) : null}
    </button>
  )
}

/** A switch with its glowing orb. */
export function Switch(p: { on: boolean; onChange?: (v: boolean) => void; label?: string; disabled?: boolean; aria?: string }) {
  const flip = () => !p.disabled && p.onChange?.(!p.on)
  return (
    <span
      role="switch"
      tabIndex={p.disabled ? -1 : 0}
      aria-checked={p.on}
      aria-disabled={p.disabled ? 'true' : undefined}
      aria-label={p.aria ?? p.label}
      onClick={flip}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          flip()
        }
      }}
    >
      <span class="tr" />
      {p.label ? <span>{p.label}</span> : null}
    </span>
  )
}

/** A segmented control. */
export function Seg<T extends string>(p: { options: readonly T[]; value: T; onChange: (v: T) => void; labels?: Partial<Record<T, string>>; disabled?: boolean }) {
  return (
    <span class="seg" data-seg>
      {p.options.map((o) => (
        <button key={o} type="button" data-v={o} aria-pressed={o === p.value} disabled={p.disabled} onClick={() => p.onChange(o)}>
          {p.labels?.[o] ?? o}
        </button>
      ))}
    </span>
  )
}

export type Tone = '' | 'vi' | 'ok' | 'warn' | 'bad' | 'teal' | 'gold' | 'c1' | 'kserver'

export function Tag(p: { children: ComponentChildren; tone?: Tone; dot?: boolean; title?: string; href?: string }) {
  if (p.href)
    return (
      <a class={`tag ${p.tone ?? ''}`} href={p.href} title={p.title} style="cursor:pointer;text-decoration:none">
        {p.dot ? <i /> : null}
        {p.children}
      </a>
    )
  return (
    <span class={`tag ${p.tone ?? ''}`} title={p.title}>
      {p.dot ? <i /> : null}
      {p.children}
    </span>
  )
}

export function StatusTag({ status }: { status: string | null | undefined }) {
  if (status === 'ok') return <Tag tone="ok" dot>{t('common.healthy')}</Tag>
  if (status === 'slow') return <Tag tone="warn" dot>{t('common.slow')}</Tag>
  if (status === 'down') return <Tag tone="bad" dot>{t('common.down')}</Tag>
  return <Tag>{t('common.notChecked')}</Tag>
}

export function Field(p: { label: ComponentChildren; children: ComponentChildren; hint?: ComponentChildren; id?: string }) {
  return (
    <div class="fld">
      <label class="lbl" for={p.id}>
        {p.label}
      </label>
      {p.children}
      {p.hint ? <div class="hint">{p.hint}</div> : null}
    </div>
  )
}

/** A panel; `tour` names the place a guided tour points at (`data-tour`, see src/tours). */
export function Panel(p: { title?: ComponentChildren; actions?: ComponentChildren; children: ComponentChildren; cls?: string; style?: string; tour?: string }) {
  return (
    <section class={`panel ${p.cls ?? ''}`} style={p.style} data-tour={p.tour}>
      {p.title ? (
        <h3>
          {p.title}
          {p.actions ? <span class="x">{p.actions}</span> : null}
        </h3>
      ) : null}
      {p.children}
    </section>
  )
}

export function Hero(p: { title: ComponentChildren; sub?: ComponentChildren; actions?: ComponentChildren; avatar?: ComponentChildren }) {
  return (
    <div class="hero">
      {p.avatar}
      <div>
        <h1>{p.title}</h1>
        {p.sub ? <p>{p.sub}</p> : null}
      </div>
      <div class="acts">{p.actions}</div>
    </div>
  )
}

export function AppAvatar({ slug, name, big }: { slug: string; name: string; big?: boolean }) {
  return (
    <span class="av" style={{ background: appColor(slug), ...(big ? { width: '44px', height: '44px', borderRadius: '13px', fontSize: '20px' } : {}) }}>
      {(name[0] ?? '?').toUpperCase()}
    </span>
  )
}

/** Tabs with the underline that glides. */
export function Tabs(p: { items: Array<{ key: string; label: string; href: string; count?: number | null }>; current: string; label: string }) {
  const nav = useRef<HTMLElement>(null)
  const place = () => {
    const t = nav.current
    if (!t) return
    const a = t.querySelector<HTMLElement>('[aria-current]')
    const b = t.querySelector<HTMLElement>('.bar')
    if (a && b) {
      b.style.left = `${a.offsetLeft + 8}px`
      b.style.width = `${a.offsetWidth - 16}px`
    }
  }
  useLayoutEffect(place)
  useEffect(() => {
    addEventListener('resize', place)
    return () => removeEventListener('resize', place)
  }, [])
  return (
    <nav class="tabs" aria-label={p.label} ref={nav}>
      {p.items.map((t) => (
        <a key={t.key} href={t.href} aria-current={t.key === p.current ? 'page' : undefined}>
          {t.label}
          {t.count != null ? <span class="n">{t.count}</span> : null}
        </a>
      ))}
      <span class="bar" />
    </nav>
  )
}

/** Key and value pairs. */
export function KV({ rows }: { rows: Array<[ComponentChildren, ComponentChildren]> }) {
  return (
    <dl class="kv">
      {rows.map(([k, v], i) => [<dt key={`k${i}`}>{k}</dt>, <dd key={`v${i}`}>{v}</dd>])}
    </dl>
  )
}

/** A bar of a share (0 to 100). */
export function Bar({ value, style }: { value: number; style?: string }) {
  return (
    <div class="bar1" style={style}>
      <i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  )
}
