/**
 * The guided tours on screen: the overlay that dims the page except the step's place (the spotlight) with the card
 * beside it, the Guides menu in the top bar, and the invite to the first tour on a first sign-in.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'preact/hooks'

import { t, tn } from '../i18n'
import { onPrefs, prefs, reducedMotion, setPrefs } from '../prefs'
import { Btn, Icon } from '../ui'
import { type Box, placeCard, spotlight } from './engine'
import { exitTour, guides, moveTour, onTour, openTour, type TourRun, tourRun, tourText } from './run'
import { FIRST_TOUR } from './tours'

/** The run as it changes. */
function useTourRun(): TourRun | null {
  const [r, setR] = useState(tourRun())
  useEffect(() => onTour(() => setR(tourRun())), [])
  return r
}

const boxOf = (el: Element): Box => {
  const r = el.getBoundingClientRect()
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}
const sameBox = (a: Box | null, b: Box) => !!a && Math.abs(a.top - b.top) < 0.5 && Math.abs(a.left - b.left) < 0.5 && Math.abs(a.width - b.width) < 0.5 && Math.abs(a.height - b.height) < 0.5
const view = () => ({ width: innerWidth, height: innerHeight })

/** Focusable controls of the card, in order (the focus stays among them while the tour runs). */
const focusables = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>('button:not([disabled]),a[href]')]

/** Keys typed in the assistant's chat are the chat's, never the tour's. */
const inChat = (e: Event) => e.composedPath().some((n) => n instanceof Element && n.localName === 'evokai-chat')

/** The overlay of the running tour: nothing while none runs. */
export function TourOverlay() {
  const run = useTourRun()
  const card = useRef<HTMLElement>(null)
  const next = useRef<HTMLButtonElement | null>(null)
  const [box, setBox] = useState<Box | null>(null)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const target = run?.target ?? null
  const moving = run?.moving ?? false

  // The page behind is out of reach (keyboard, pointer, screen readers) while the tour runs.
  const on = !!run
  useEffect(() => {
    if (!on) return
    const behind = [...document.querySelectorAll<HTMLElement>('.shell > .side, .shell > .content')]
    for (const el of behind) el.inert = true
    return () => {
      for (const el of behind) el.inert = false
      setPos(null)
      setBox(null)
    }
  }, [on])

  // A new place: into view, then the spotlight follows it (the page may still move as its data arrives).
  useEffect(() => {
    if (!target) return setBox(null)
    const tall = target.getBoundingClientRect().height > innerHeight * 0.6
    target.scrollIntoView({ block: tall ? 'start' : 'center', inline: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' })
    let frame = 0
    let last: Box | null = null
    const follow = () => {
      const b = boxOf(target)
      if (!sameBox(last, b)) {
        last = b
        setBox(b)
      }
      frame = requestAnimationFrame(follow)
    }
    follow()
    return () => cancelAnimationFrame(frame)
  }, [target])

  // The card beside the spotlight, once its size is known.
  // While the next page opens there is no spotlight: the page is dimmed and the card waits where it was.
  const spot = box && !moving ? spotlight(box, view()) : null
  useLayoutEffect(() => {
    const c = card.current
    if (!c || !spot) return
    const p = placeCard(spot, { width: c.offsetWidth, height: c.offsetHeight }, view(), run?.steps[run.index]?.placement)
    if (!pos || Math.abs(pos.top - p.top) > 0.5 || Math.abs(pos.left - p.left) > 0.5) setPos({ top: p.top, left: p.left })
  })

  // Each step shown: the focus on Next (unless the person is typing to the assistant).
  useEffect(() => {
    if (!run || moving) return
    const active = document.activeElement
    if (active && active.localName === 'evokai-chat') return
    if (!card.current?.contains(active)) next.current?.focus()
  }, [run?.index, moving, on])

  // The keyboard: Esc leaves, arrows and Enter move, Tab stays in the card.
  useEffect(() => {
    if (!on) return
    const key = (e: KeyboardEvent) => {
      if (inChat(e)) return
      const c = card.current
      const at = e.target as Element | null
      const inCard = !!c && !!at && c.contains(at)
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        // The palette would open under the tour: not while one runs.
        e.preventDefault()
        e.stopPropagation()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        exitTour()
      } else if (e.key === 'ArrowRight' || (e.key === 'Enter' && !(inCard && at?.matches('button,a')))) {
        e.preventDefault()
        moveTour(1)
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        moveTour(-1)
      } else if (e.key === 'Tab' && c) {
        const all = focusables(c)
        if (!all.length) return
        const i = all.indexOf(document.activeElement as HTMLElement)
        e.preventDefault()
        const to = !inCard || i < 0 ? 0 : (i + (e.shiftKey ? -1 : 1) + all.length) % all.length
        all[to]!.focus()
      }
    }
    addEventListener('keydown', key, true)
    return () => removeEventListener('keydown', key, true)
  }, [on])

  if (!run) return null
  const step = run.steps[run.index]!
  const id = run.tour.id
  const last = run.index === run.steps.length - 1
  const n = run.index + 1
  const total = run.steps.length
  return (
    <div class={`tour ${moving ? 'moving' : ''}`}>
      {/* The dim page: a click there does nothing (Exit leaves). */}
      <div class="tour-block" aria-hidden="true" />
      {spot ? (
        <div class="tour-spot" aria-hidden="true" style={{ top: `${spot.top}px`, left: `${spot.left}px`, width: `${spot.width}px`, height: `${spot.height}px` }} />
      ) : (
        <div class="tour-dim" aria-hidden="true" />
      )}
      <section
        ref={card}
        class={`tour-card ${pos ? '' : 'centred'}`}
        role="dialog"
        aria-modal="true"
        aria-label={t('tour.dialog', { title: tourText(id, 'title') })}
        aria-describedby="tour-body"
        aria-busy={moving}
        style={pos ? { top: `${pos.top}px`, left: `${pos.left}px` } : undefined}
      >
        <div class="tour-head">
          <span>{tourText(id, 'title')}</span>
          <span class="tour-n">{t('tour.progress', { n, total })}</span>
        </div>
        <div class="tour-bar" aria-hidden="true">
          <i style={{ width: `${(n / total) * 100}%` }} />
        </div>
        <div aria-live="polite">
          <h2 id="tour-title">{tourText(id, `${step.id}.title`)}</h2>
          <p id="tour-body">{moving ? t('tour.opening') : tourText(id, `${step.id}.body`)}</p>
          {run.skipped && !moving ? <p class="hint">{tn('tour.skipped', run.skipped)}</p> : null}
        </div>
        <div class="tour-acts">
          <Btn sm kind="ghost" onClick={() => exitTour()}>
            {t('tour.exit')}
          </Btn>
          {run.index > 0 ? (
            <Btn sm onClick={() => moveTour(-1)}>
              {t('tour.back')}
            </Btn>
          ) : null}
          <button ref={next} type="button" class="btn pri sm" onClick={() => moveTour(1)}>
            <span class="lb">{last ? t('tour.finish') : t('tour.next')}</span>
          </button>
        </div>
        <p class="tour-keys">{t('tour.keys')}</p>
      </section>
    </div>
  )
}

/** The Guides menu in the top bar: every tour this person can take, where they stand, and Start or Resume. */
export function GuidesMenu() {
  const [open, setOpen] = useState(false)
  const btn = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  const [, setN] = useState(0)
  useEffect(() => onPrefs(() => setN((x) => x + 1)), [])
  useEffect(() => {
    if (!open) return
    pop.current?.querySelector<HTMLElement>('button')?.focus()
    const away = (e: MouseEvent) => {
      if (!(e.target as Element).closest('.guides')) setOpen(false)
    }
    addEventListener('click', away)
    return () => removeEventListener('click', away)
  }, [open])
  const close = () => {
    setOpen(false)
    btn.current?.focus()
  }
  const list = open ? guides() : []
  return (
    <div class="guides">
      <button ref={btn} type="button" class="btn ghost sm guidesbtn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="guide" />
        <span>{t('tour.guides')}</span>
      </button>
      {open ? (
        <div
          ref={pop}
          class="guides-pop"
          role="dialog"
          aria-label={t('tour.guidesTitle')}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              close()
            }
          }}
        >
          <h6>{t('tour.guidesTitle')}</h6>
          <p class="hint">{t('tour.guidesHint')}</p>
          <ul>
            {list.map(({ tour, steps, status }) => (
              <li key={tour.id}>
                <div>
                  <b>{tourText(tour.id, 'title')}</b>
                  <small>{tourText(tour.id, 'sub')}</small>
                  <small class="st">
                    {status.kind === 'done'
                      ? t('tour.done')
                      : status.kind === 'stopped'
                        ? t('tour.stopped', { n: status.step, total: steps.length })
                        : tn('tour.nSteps', steps.length)}
                  </small>
                </div>
                <Btn
                  sm
                  kind={status.kind === 'stopped' ? 'pri' : ''}
                  onClick={() => {
                    setOpen(false)
                    openTour(tour.id, status.kind === 'done')
                  }}
                >
                  {status.kind === 'stopped' ? t('tour.resume') : status.kind === 'done' ? t('tour.again') : t('tour.start')}
                </Btn>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/** The invite to the first tour: until the person starts a tour or says not now (kept in their preferences). */
export function TourInvite() {
  const run = useTourRun()
  const [invite, setInvite] = useState(prefs().tourInvite)
  useEffect(() => onPrefs((p) => setInvite(p.tourInvite)), [])
  if (invite !== 'show' || run || !guides().some((g) => g.tour.id === FIRST_TOUR)) return null
  return (
    <section class="tour-invite" aria-label={t('tour.invite.label')}>
      <Icon name="guide" />
      <div>
        <b>{t('tour.invite.title')}</b>
        <p>{t('tour.invite.text')}</p>
      </div>
      <span class="x">
        <Btn sm kind="pri" onClick={() => openTour(FIRST_TOUR)}>
          {t('tour.invite.start')}
        </Btn>
        <Btn sm kind="ghost" onClick={() => setPrefs({ tourInvite: 'dismissed' })}>
          {t('tour.invite.later')}
        </Btn>
      </span>
    </section>
  )
}
