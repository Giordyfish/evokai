/**
 * Voice mode's pieces: the status, the subtitles (the spoken part full, the rest faded), the keyboard, mute and end
 * controls and the «interrupt by speaking» switch. The chat panel shows them inside its box (see `panel.tsx`); the
 * standalone `<evokai-voice>` shows them on the full-screen stage below: the page blurs behind a veil, the orb stands
 * in the middle and the latest charts sit on the side. The stage is a modal dialog: the focus stays inside (Tab
 * cycles), Escape ends voice mode.
 *
 * The orb reacts to one measurement for every source (the client's audio signals): while the user speaks it grows with
 * the level; while the agent speaks only the plasma moves, faster as the level rises.
 */
import type { ComponentChildren, VNode } from 'preact'
import { useEffect, useRef } from 'preact/hooks'

import type { Controller, ControllerState } from './controller'
import { useUi } from './context'
import { createOrb, newMotion, orbFallback, reducedMotion, stepMotion, swell } from './orb'
import { Icon } from './icons'
import { fill, type Strings } from './strings'
import type { OrbColors } from './theme'

const SIZE = 260

export type StagePhase = 'listening' | 'user' | 'thinking' | 'speaking'

export function stagePhase(st: ControllerState): StagePhase {
  const p = st.session.phase
  if (p === 'speaking') return 'speaking'
  if (p === 'thinking') return 'thinking'
  if (st.heard) return 'user'
  return 'listening'
}

/**
 * The status line of voice mode (`bare` drops the trailing ellipsis, for a label in capitals or with dots). After the
 * user talked over the agent (the phase is `interrupted`) the voice behaves as listening; only the text says so.
 */
export function voiceStatus(s: Strings, st: ControllerState, bare = false): string {
  const phase = stagePhase(st)
  const t = st.muted
    ? s.micOff
    : phase === 'speaking'
      ? s.speaking
      : phase === 'thinking'
        ? s.thinking
        : st.session.phase === 'interrupted'
          ? s.interrupted
          : s.listening
  return bare ? t.replace(/(…|\.\.\.)$/, '') : t
}

/** Keep the focus inside an element: Tab and Shift+Tab cycle through its controls. */
export function trapFocus(root: HTMLElement, e: KeyboardEvent): void {
  if (e.key !== 'Tab') return
  const items = [...root.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input, textarea, [tabindex]:not([tabindex="-1"])')]
  if (!items.length) return
  const first = items[0] as HTMLElement
  const last = items[items.length - 1] as HTMLElement
  const active = (root.getRootNode() as Document | ShadowRoot).activeElement
  if (e.shiftKey && (active === first || !root.contains(active))) {
    e.preventDefault()
    last.focus()
  } else if (!e.shiftKey && active === last) {
    e.preventDefault()
    first.focus()
  }
}

/** Where the agent's line is cut between what was said and what is still to say (at the end of the current word). */
export function lineCut(line: { text: string; upto: number }): number {
  if (!line.text) return 0
  const k = line.text.indexOf(' ', line.upto)
  return k < 0 ? line.text.length : k
}

/** What the user is saying, or the agent's line with its spoken part full and the rest faded. */
export function Subtitles({ st }: { st: ControllerState }): VNode {
  const line = st.line
  const cut = lineCut(line)
  return (
    <p class="subtitles" part="subtitles" aria-live="polite">
      {stagePhase(st) === 'user' || (!line.text && st.heard) ? (
        <span class="sub">{st.heard}</span>
      ) : (
        <>
          <span class="said" part="said">
            {line.text.slice(0, cut)}
          </span>
          <span class="tosay" part="to-say">
            {line.text.slice(cut)}
          </span>
        </>
      )}
    </p>
  )
}

/**
 * Mute and end (back to the chat), and the «interrupt by speaking» switch under them; the keyboard button only where
 * ending has another meaning (the full-screen stage), never in the panel, where it did what the end button does.
 */
export function VoiceControls(props: { controller: Controller; state: ControllerState; onKeyboard?: () => void; onEnd: () => void; size?: number }): VNode {
  const { controller: c, state: st } = props
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  const size = props.size ?? 20
  return (
    <>
      <div class="controls" part="stage-controls">
        {props.onKeyboard ? (
          <button type="button" class="round" part="stage-button keyboard-button" aria-label={s.keyboard} title={s.keyboard} onClick={props.onKeyboard}>
            <Icon d="keyboard" size={size} />
          </button>
        ) : null}
        <button
          type="button"
          class={`round ${st.muted ? 'muted' : ''}`}
          part="stage-button mute-button"
          aria-pressed={st.muted}
          aria-label={st.muted ? s.unmute : s.mute}
          title={st.muted ? s.unmute : s.mute}
          onClick={() => c.setMuted(!st.muted)}
        >
          <Icon d="mic" size={size} />
        </button>
        <button type="button" class="round finish" part="stage-button end-button" aria-label={s.endVoice} title={s.endVoice} onClick={props.onEnd}>
          <Icon d="x" size={size} />
        </button>
      </div>
      <button
        type="button"
        role="switch"
        class="switch"
        part="talk-over"
        aria-checked={st.talkOver}
        title={t(st.talkOver ? s.talkOverOn : s.talkOverOff)}
        onClick={() => c.setTalkOver(!st.talkOver)}
      >
        <span class="track" aria-hidden="true">
          <span class="knob" />
        </span>
        {s.talkOver}
      </button>
    </>
  )
}

/** The full-screen stage of `<evokai-voice>`. */
export function VoiceStage(props: {
  controller: Controller
  state: ControllerState
  cards: ComponentChildren
  orb?: Partial<OrbColors> | undefined
  onKeyboard: () => void
  onEnd: () => void
}): VNode {
  const { controller: c, state: st } = props
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  const phase = stagePhase(st)
  const root = useRef<HTMLDivElement>(null)
  const orbRef = useRef<HTMLCanvasElement>(null)
  const ringRef = useRef<HTMLCanvasElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const live = useRef({ phase, muted: st.muted || !st.micOpen })
  live.current = { phase, muted: st.muted || !st.micOpen }
  const key = JSON.stringify(props.orb ?? {})

  // Focus the first control when the stage opens.
  useEffect(() => {
    root.current?.querySelector<HTMLElement>('button')?.focus()
  }, [])

  useEffect(() => {
    const orbCanvas = orbRef.current
    const ring = ringRef.current
    const wrap = wrapRef.current
    if (!orbCanvas || !ring || !wrap) return
    const orb = createOrb(orbCanvas, SIZE, props.orb, 0.92)
    const still = reducedMotion()
    const rc = ring.getContext('2d')
    const dpr = Math.min(2, devicePixelRatio || 1)
    const RS = (ring.width = ring.height = Math.round(SIZE * 2.1 * dpr))
    const m = newMotion()
    let last = performance.now()
    let raf = 0
    const accent = getComputedStyle(root.current as Element).getPropertyValue('--ek-accent').trim() || '#a78bfa'
    const frame = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const { phase: ph, muted } = live.current
      const { frame: f, mode } = stepMotion(m, { phase: ph, muted, signals: c.voice?.signals, now, dt })
      orb?.draw(f)
      const scale = swell(m.level, mode)
      wrap.style.transform = `scale(${scale.toFixed(3)})`
      if (rc) {
        rc.clearRect(0, 0, RS, RS)
        if (ph === 'thinking') {
          const a0 = now / 380
          const c0 = RS / 2
          rc.strokeStyle = accent
          rc.globalAlpha = 0.85
          rc.lineWidth = RS / 180
          rc.lineCap = 'round'
          rc.beginPath()
          rc.arc(c0, c0, (RS / 2.1) * 0.5 * scale + 14, a0, a0 + 1.1)
          rc.stroke()
        }
      }
      if (!still) raf = requestAnimationFrame(frame)
    }
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      orb?.dispose()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c, key])

  return (
    <div
      ref={root}
      class="stage"
      part="stage"
      data-phase={phase}
      role="dialog"
      aria-modal="true"
      aria-label={t(s.voiceDialog)}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          props.onEnd()
        } else if (root.current) trapFocus(root.current, e)
      }}
    >
      <div class="center">
        <div class="orbwrap" part="stage-orb">
          <slot name="stage-orb">
            <canvas ref={ringRef} aria-hidden="true" class="ringfx" style={{ width: `${SIZE * 2.1}px`, height: `${SIZE * 2.1}px` }} />
            <div ref={wrapRef} class="big">
              <canvas ref={orbRef} aria-hidden="true" style={{ width: `${SIZE}px`, height: `${SIZE}px`, background: orbFallback(props.orb) }} />
            </div>
          </slot>
        </div>
        <div class="status" part="stage-status" aria-live="polite">
          {voiceStatus(s, st)}
        </div>
        {st.noVoice ? (
          <p class="sub" part="stage-notice" role="status">
            {s.speechUnavailable}
          </p>
        ) : null}
        <Subtitles st={st} />
        <VoiceControls controller={c} state={st} onKeyboard={props.onKeyboard} onEnd={props.onEnd} />
      </div>
      {props.cards ? (
        <aside class="cards" part="stage-cards" aria-label={s.details}>
          {props.cards}
        </aside>
      ) : null}
    </div>
  )
}
