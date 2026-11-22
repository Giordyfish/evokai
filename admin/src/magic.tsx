/**
 * The console's magic, as in the design: the mage (only on sign in, empty states, errors and the playground's
 * thinking), the rune circle and the dark flame (the frequent motifs), embers, sparkles, motes, the drawing ring, the
 * spell skeleton and the top progress line. Every animation has a still fallback for reduced motion, which follows
 * the person's preference (`src/prefs.ts`) and otherwise the system's.
 */
import type { ComponentChildren, CSSProperties } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'
import { t } from './i18n'
import { brandMark, mascotMoves, onPrefs, prefs, reducedMotion } from './prefs'

/** Whether animations are reduced now (read at each use: the preference can change at any time). */
export const RM = (): boolean => reducedMotion()

const FX = 'fx/'
const MAS = {
  combo: 'idle-combo-256.webp',
  rune: 'spell-rune-circle-256.webp',
  blink: 'idle-blink-128.webp',
  ember: 'idle-ember-128.webp',
  hop: 'spell-hop-128.webp',
} as const
const FXS = {
  flame: 'fx-dark-flame-128.webp',
  flameLoop: 'fx-dark-flame-loop-128.webp',
  sparkles: 'fx-sparkles-128.webp',
  embers: 'fx-embers-128.webp',
} as const
const STILL: Partial<Record<keyof typeof FXS, string>> = {
  flame: 'fx-dark-flame-still-128.png',
  flameLoop: 'fx-dark-flame-still-128.png',
  embers: 'fx-embers-still-128.png',
}
export type MageKind = keyof typeof MAS
export type FxKind = keyof typeof FXS

/** A query that restarts a one-shot animation. */
export const bust = (): string => `?r=${Date.now()}${Math.floor(Math.random() * 1e4)}`

export const masSrc = (k: MageKind, size: number): string =>
  FX + (!mascotMoves() ? (size > 130 ? 'still-256.png' : 'still-128.png') : MAS[k] + (k === 'hop' || k === 'rune' ? bust() : ''))

/** The mage. */
export function Mage(p: { k: MageKind; size: number; cls?: string; alt?: string; id?: string }) {
  return (
    <img
      class={`mas ${p.cls ?? ''}`}
      id={p.id}
      src={masSrc(p.k, p.size)}
      width={p.size}
      height={p.size}
      alt={p.alt ?? ''}
      decoding="async"
    />
  )
}

export const fxSrc = (k: FxKind, once = false): string | null =>
  RM() ? (STILL[k] ? FX + STILL[k] : null) : FX + FXS[k] + (once ? bust() : '')

/** An effect image (dark flame, embers, sparkles); nothing under reduced motion when it has no still. */
export function Fx(p: { k: FxKind; size: number; cls?: string; once?: boolean }) {
  const src = fxSrc(p.k, p.once)
  if (!src) return null
  return <img class={`fx ${p.cls ?? ''}`} src={src} width={p.size} height={p.size} alt="" decoding="async" />
}

/** The rune circle: draws itself (`draw`), then turns and glows (`turn`). */
export function Rune(p: { size: number; cls?: string }) {
  return <span class={`rune ${p.size > 200 ? 'big' : ''} ${p.cls ?? ''}`} style={{ width: `${p.size}px` }} aria-hidden="true" />
}

const ringTicks = [0, 1, 2, 3, 4, 5].map((i) => {
  const a = (i * Math.PI) / 3
  return {
    x1: (10 + Math.sin(a) * 8.4).toFixed(2),
    y1: (10 - Math.cos(a) * 8.4).toFixed(2),
    x2: (10 + Math.sin(a) * 9.6).toFixed(2),
    y2: (10 - Math.cos(a) * 9.6).toFixed(2),
  }
})

/** The small ring that draws and erases itself in a working button. */
export function Ring() {
  return (
    <svg class="rr" viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="7" pathLength={100} />
      {ringTicks.map((t, i) => (
        <line key={i} {...t} />
      ))}
    </svg>
  )
}

/** Orbiting motes while a tool runs. */
export function Motes() {
  return (
    <span class="motes" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  )
}

/** Skeleton rows crossed by a spell line. */
export function Skeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div class="skel" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <span
          key={i}
          class="sk"
          style={{ '--d': `${(i * 0.14).toFixed(2)}s`, width: `${[94, 78, 86, 62, 72][i % 5]}%` } as CSSProperties}
        />
      ))}
    </div>
  )
}

/** The health orb: embers rise while everything answers; still and grey otherwise. */
export function HealthOrb({ ok }: { ok: boolean }) {
  return (
    <span class={`hico ${ok ? '' : 'dim'}`} aria-hidden="true">
      {ok ? <Fx k="embers" size={84} /> : null}
    </span>
  )
}

/** An empty state: the mage, a line, an action. */
export function Empty(p: { k?: MageKind; size?: number; children: ComponentChildren; action?: ComponentChildren }) {
  return (
    <div class="empty">
      <Mage k={p.k ?? 'blink'} size={p.size ?? 96} />
      <p>{p.children}</p>
      {p.action}
    </div>
  )
}

/** The page loader: the rune circle draws itself, then turns. */
export function PageLoader({ what }: { what: string }) {
  return (
    <div class="ploader" role="status">
      <Rune size={132} cls="turn" />
      <span>{t('common.opening', { what })}</span>
    </div>
  )
}

/** The page's zoom (the text size preference): fixed positions are written in zoomed pixels. */
const pageZoom = (): number => Number(getComputedStyle(document.documentElement).zoom) || 1

/** Sparkles where something was confirmed or saved. */
export function sparkle(el: Element | null | undefined): void {
  if (RM() || !el) return
  const r = el.getBoundingClientRect()
  const zoom = pageZoom()
  const cx = (r.left + r.width / 2) / zoom
  const cy = (r.top + r.height / 2) / zoom
  if (recent(el)) return
  const cols = ['var(--c1)', 'var(--attn)', 'var(--c2)']
  for (let i = 0; i < 9; i++) {
    const s = document.createElement('span')
    s.className = 'spk'
    s.style.color = cols[i % 3]!
    const a = Math.random() * Math.PI * 2
    const d = 30 + Math.random() * 30
    const z = 3 + Math.random() * 4
    s.style.width = s.style.height = `${z}px`
    s.style.left = `${cx - z / 2}px`
    s.style.top = `${cy - z / 2}px`
    s.style.setProperty('--dx', `${Math.cos(a) * d}px`)
    s.style.setProperty('--dy', `${Math.sin(a) * d}px`)
    s.style.animationDelay = `${i * 18}ms`
    document.body.append(s)
    setTimeout(() => s.remove(), 1000)
  }
}

// Effects start on pointerdown from a pool of pre-decoded images, so nothing waits for a load.

const POOL: Partial<Record<FxKind, HTMLImageElement[]>> = {}

function fillPool(k: FxKind): void {
  if (RM()) return
  const a = (POOL[k] ??= [])
  while (a.length < 2) {
    const i = new Image()
    i.src = FX + FXS[k] + bust()
    i.decode?.().catch(() => {})
    a.push(i)
  }
}

/** A fresh, already decoded copy of an effect (it restarts from its first frame). */
export function takeFx(k: FxKind, size: number): HTMLImageElement | null {
  if (RM()) return null
  const a = POOL[k] ?? []
  let i = a.find((x) => x.complete) ?? a[0]
  if (i) a.splice(a.indexOf(i), 1)
  else {
    i = new Image()
    i.src = FX + FXS[k] + bust()
  }
  i.width = i.height = size
  i.alt = ''
  i.className = 'fx'
  setTimeout(() => fillPool(k), 60)
  return i
}

const pressed = new WeakMap<Element, number>()
const recent = (el: Element): boolean => Date.now() - (pressed.get(el) ?? 0) < 1500

/**
 * Buttons marked `data-fx` start their effect as the pointer goes down: `save` sparkles, `confirm` sparkles under
 * the sparkles effect, `key` sparkles (the key's flame is lit by its slip).
 */
export function installPointerFx(): void {
  ;(['flame', 'sparkles'] as FxKind[]).forEach(fillPool)
  document.addEventListener(
    'pointerdown',
    (e) => {
      const b = (e.target as Element | null)?.closest?.('[data-fx]') as HTMLButtonElement | null
      if (!b || b.disabled) return
      sparkle(b)
      pressed.set(b, Date.now())
      if (b.dataset.fx === 'confirm') oneshot(b, 'sparkles', 110, 2600)
    },
    { passive: true },
  )
}

/** A one-shot effect over an element (sparkles when a test passes or a change is confirmed). */
export function oneshot(el: Element | null | undefined, k: FxKind, size: number, msShown: number): void {
  if (RM() || !el) return
  if (k === 'sparkles' && recent(el)) return
  const r = el.getBoundingClientRect()
  const z = pageZoom()
  const i = takeFx(k, size)
  if (!i) return
  i.className = 'oneshot'
  i.style.left = `${(r.left + r.width / 2) / z - size / 2}px`
  i.style.top = `${(r.top + r.height / 2) / z - size / 2 - size * 0.18}px`
  document.body.append(i)
  setTimeout(() => i.remove(), msShown)
}

/** The check that draws itself on a saved button. */
export function done(el: Element | null | undefined): void {
  if (!el || !el.classList.contains('btn')) return
  el.classList.add('done')
  setTimeout(() => el.classList.remove('done'), 1300)
}

let running = 0

/** The thin line with dust at its head, above every load. */
export function progress(state: 'run' | 'done'): void {
  const p = document.getElementById('prog')
  if (!p) return
  if (state === 'run') {
    running += 1
    if (running > 1) return
    p.style.width = ''
    p.className = 'progress'
    void p.offsetWidth
    p.className = 'progress run'
    return
  }
  running = Math.max(0, running - 1)
  if (running) return
  p.style.width = getComputedStyle(p).width
  p.className = 'progress'
  void p.offsetWidth
  p.className = 'progress done'
}

// Toast

type ToastState = { title: string; sub: string; fx: FxKind | 'still' | null; on: boolean; n: number; attn: boolean }
let toastState: ToastState = { title: '', sub: '', fx: null, on: false, n: 0, attn: false }
const toastListeners = new Set<(s: ToastState) => void>()
let toastTimer: ReturnType<typeof setTimeout> | undefined

/** A toast; the dark flame lights in it by default. */
export function toast(title: string, sub = t('common.justNow'), fx: FxKind | null = 'flame'): void {
  toastState = { title, sub, fx: fx ?? null, on: true, n: toastState.n + 1, attn: /key|Copy|[cC]hiave|Copia/.test(sub + title) }
  toastListeners.forEach((f) => f(toastState))
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toastState = { ...toastState, on: false }
    toastListeners.forEach((f) => f(toastState))
  }, 3000)
}

export function onToast(f: (s: ToastState) => void): () => void {
  toastListeners.add(f)
  return () => toastListeners.delete(f)
}

/** Preload and decode the effects shortly after start, so the first toast or test does not wait. */
export function preload(): void {
  if (RM()) return
  setTimeout(() => {
    for (const f of [...Object.values(FXS), MAS.ember, MAS.blink]) {
      const i = new Image()
      i.src = FX + f
      i.decode?.().catch(() => {})
    }
  }, 600)
}

// The familiar: a small mage living in the sidebar. Random idles; a spontaneous spell every 25 to 45 seconds,
// never two in a row; a click casts one. Paused while the page is hidden; still under reduced motion or when the
// person wants it still, and gone when they hide it.

const F_IDLE: Array<[string, number]> = [
  ['idle-combo', 3],
  ['idle-blink', 2],
  ['idle-breathe', 2],
  ['idle-hat-sway', 2],
  ['idle-ember', 2],
]
const F_SPELL: Record<string, number> = {
  'spell-staff-sparkles': 2600,
  'spell-dark-flame': 3000,
  'spell-rune-circle': 3200,
  'spell-orbit': 3000,
  'spell-hop': 1800,
}

function wpick(a: Array<[string, number]>): string {
  let r = Math.random() * a.reduce((x, y) => x + y[1], 0)
  for (const [k, w] of a) if ((r -= w) < 0) return k
  return a[0]![0]
}

/** The mark beside Evokai now: re-rendered when the preferences, the system or the assistant change. */
function useBrandMark(assistantActive: boolean): 'logo' | 'still' | 'moving' {
  const read = () => brandMark(prefs(), assistantActive, reducedMotion())
  const [m, setM] = useState(read)
  useEffect(() => {
    setM(read())
    return onPrefs(() => setM(read()))
  }, [assistantActive])
  return m
}

/**
 * The mark beside Evokai in the sidebar header: the logo, or the small mage the person chose in Preferences (its idle
 * moves and, now and then, a spell). Always the logo while the console assistant is active.
 */
export function BrandMark({ assistantActive }: { assistantActive: boolean }) {
  const mode = useBrandMark(assistantActive)
  const img = useRef<HTMLImageElement>(null)
  const st = useRef({ t: 0 as ReturnType<typeof setTimeout> | 0, mode: 'idle', cur: '', spellAt: 0 })
  useEffect(() => {
    if (mode !== 'moving') return
    const s = st.current
    const gap = () => Date.now() + 25000 + Math.random() * 20000
    s.spellAt = gap()
    const show = (src: string) => {
      const i = new Image()
      i.src = src
      ;(i.decode ? i.decode() : Promise.resolve())
        .catch(() => {})
        .then(() => {
          if (img.current) img.current.src = src
        })
    }
    const idle = () => {
      clearTimeout(s.t)
      if (document.hidden) return
      let k = wpick(F_IDLE)
      if (k === s.cur) k = wpick(F_IDLE)
      s.cur = k
      s.mode = 'idle'
      show(`${FX}${k}-128.webp`)
      const left = s.spellAt - Date.now()
      s.t = setTimeout(next, Math.max(6000, Math.min(8000 + Math.random() * 12000, left)))
    }
    const next = () => {
      if (document.hidden) return
      if (s.mode === 'idle' && Date.now() >= s.spellAt - 500) spell()
      else idle()
    }
    const spell = () => {
      clearTimeout(s.t)
      const ks = Object.keys(F_SPELL)
      const name = ks[Math.floor(Math.random() * ks.length)]!
      s.mode = 'spell'
      s.spellAt = gap()
      show(`${FX}${name}-128.webp${bust()}`)
      s.t = setTimeout(idle, F_SPELL[name]! + 80)
    }
    const vis = () => (document.hidden ? clearTimeout(s.t) : idle())
    document.addEventListener('visibilitychange', vis)
    s.t = setTimeout(idle, 1200)
    return () => {
      clearTimeout(s.t)
      document.removeEventListener('visibilitychange', vis)
    }
  }, [mode])
  if (mode === 'logo') return <img src={HAT} alt="" />
  return <img ref={img} class="bmage" alt="" width={44} height={44} src={FX + (mode === 'moving' ? 'idle-blink-128.webp' : 'still-128.png')} />
}

export const HAT = 'brand/hat.png'
