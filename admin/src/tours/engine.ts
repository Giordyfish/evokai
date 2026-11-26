/**
 * The guided tours' logic, with no drawing and no DOM: which steps a person gets, where a step goes, moving and
 * resuming, waiting for a page's place to appear (skipping a step whose place never does), where the card goes, and
 * what the assistant is told about the step on screen.
 */
import type { Need, Placement, Tour, TourStep } from './tours'

/** What decides which steps a person gets. */
export interface TourEnv {
  /** The slug of the application the tour shows: the one open, or the first; null when there is none. */
  app: string | null
  canCreate: boolean
  owner: boolean
}

/** A step ready to run: its route with the application filled in. */
export interface ResolvedStep extends TourStep {
  path: string
}

/** What a step needs: its own list, and an application when its route names one. */
export function needsOf(step: TourStep): Need[] {
  const own = step.needs ?? []
  return step.route.includes('{app}') && !own.includes('app') ? ['app', ...own] : own
}

const met = (need: Need, env: TourEnv): boolean => (need === 'app' ? !!env.app : need === 'create' ? env.canCreate : env.owner)

/** A route with `{app}` replaced by the application's slug. */
export const fillRoute = (route: string, app: string | null): string => route.replaceAll('{app}', encodeURIComponent(app ?? ''))

/** The steps of a tour this person gets, in order, their routes filled in. */
export function stepsFor(tour: Tour, env: TourEnv): ResolvedStep[] {
  return tour.steps.filter((s) => needsOf(s).every((n) => met(n, env))).map((s) => ({ ...s, path: fillRoute(s.route, env.app) }))
}

/** The application a tour shows: the one in the open route (`/apps/{slug}/...`), else the first listed. */
export function tourApp(hash: string, slugs: string[]): string | null {
  const m = /^#?\/apps\/([^/?]+)/.exec(hash)
  const open = m ? decodeURIComponent(m[1]!) : null
  if (open && slugs.includes(open)) return open
  return slugs[0] ?? null
}

/**
 * Whether the console already shows a step's route: the same path, and every query parameter of the route with the
 * same value (the playground's `?app=` may carry an agent too).
 */
export function atRoute(hash: string, path: string): boolean {
  const [hp = '', hq = ''] = hash.replace(/^#/, '').split('?')
  const [sp = '', sq = ''] = path.split('?')
  const norm = (p: string) => (p.replace(/\/+$/, '') || '/').toLowerCase()
  if (norm(hp) !== norm(sp)) return false
  const have = new URLSearchParams(hq)
  return [...new URLSearchParams(sq)].every(([k, v]) => have.get(k) === v)
}

// Moving

export type Direction = 1 | -1

/**
 * The step a move asks for: the next or the previous one; `done` past the last; null before the first (Back on the
 * first step stays).
 */
export function moveTo(index: number, dir: Direction, total: number): number | 'done' | null {
  const to = index + dir
  if (to >= total) return 'done'
  if (to < 0) return null
  return to
}

/**
 * Find the step to show from `from` going `dir`: the first whose place `locate` finds. Steps it does not find are
 * skipped (and listed), so a missing place never stops the tour.
 *
 * @returns The index (null when no step in that direction has its place) and the indexes skipped.
 */
export async function findStep(total: number, from: number, dir: Direction, locate: (index: number) => Promise<boolean>): Promise<{ index: number | null; skipped: number[] }> {
  const skipped: number[] = []
  for (let i = from; i >= 0 && i < total; i += dir) {
    if (await locate(i)) return { index: i, skipped }
    skipped.push(i)
  }
  return { index: null, skipped }
}

export const WAIT_MS = 6000
export const POLL_MS = 100

/**
 * Wait for something to appear (a page's place, once its route rendered and its data loaded): `find` is asked every
 * `every` ms until it answers or `ms` passed.
 *
 * @returns What `find` found, or null after the time.
 */
export async function waitFor<T>(
  find: () => T | null,
  ms = WAIT_MS,
  every = POLL_MS,
  clock: { now: () => number; sleep: (ms: number) => Promise<void> } = { now: () => Date.now(), sleep: (n) => new Promise((r) => setTimeout(r, n)) },
): Promise<T | null> {
  const end = clock.now() + ms
  for (;;) {
    const found = find()
    if (found) return found
    if (clock.now() >= end) return null
    await clock.sleep(every)
  }
}

// Resuming

/** Where a tour starts: the step it stopped at, or the first when it was never started, was finished, or is out of range. */
export function resumeAt(saved: number | undefined, total: number): number {
  if (saved == null || !Number.isInteger(saved) || saved < 0 || saved >= total) return 0
  return saved
}

export type TourStatus = { kind: 'new' } | { kind: 'stopped'; step: number } | { kind: 'done' }

/** What the Guides menu says of a tour: not started, stopped at a step (1-based), or finished. */
export function statusOf(saved: number | undefined, total: number): TourStatus {
  if (saved == null || !Number.isInteger(saved) || saved <= 0) return { kind: 'new' }
  if (saved >= total) return { kind: 'done' }
  return { kind: 'stopped', step: saved + 1 }
}

/** The step a tour resumes at, from this browser's copy (written at every step) or else the person's preferences. */
export function savedStep(id: string, local: Record<string, number>, prefs: Record<string, number>): number | undefined {
  return local[id] ?? prefs[id]
}

// Placing the card

export interface Box {
  top: number
  left: number
  width: number
  height: number
}

export interface Size {
  width: number
  height: number
}

export type Side = Exclude<Placement, 'auto'> | 'over'

/** The spotlight: the place with some room around it, kept inside the screen. */
export function spotlight(target: Box, view: Size, pad = 6): Box {
  const top = Math.max(4, target.top - pad)
  const left = Math.max(4, target.left - pad)
  const bottom = Math.min(view.height - 4, target.top + target.height + pad)
  const right = Math.min(view.width - 4, target.left + target.width + pad)
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) }
}

/**
 * Where the card goes beside the spotlight: the side asked for when it fits, else the first that fits of below,
 * above, right and left; when none does (a place larger than the screen), over the place at the bottom of the screen.
 * Always inside the screen, `margin` from its edges.
 */
export function placeCard(spot: Box, card: Size, view: Size, prefer: Placement = 'auto', gap = 14, margin = 16): { top: number; left: number; side: Side } {
  const room = {
    bottom: view.height - (spot.top + spot.height) - gap - margin,
    top: spot.top - gap - margin,
    right: view.width - (spot.left + spot.width) - gap - margin,
    left: spot.left - gap - margin,
  }
  const fits = (s: Exclude<Side, 'over'>) => (s === 'top' || s === 'bottom' ? room[s] >= card.height : room[s] >= card.width)
  const order: Array<Exclude<Side, 'over'>> = ['bottom', 'top', 'right', 'left']
  const side = prefer !== 'auto' && fits(prefer) ? prefer : order.find(fits)
  const clampX = (x: number) => Math.min(Math.max(margin, x), Math.max(margin, view.width - card.width - margin))
  const clampY = (y: number) => Math.min(Math.max(margin, y), Math.max(margin, view.height - card.height - margin))
  switch (side) {
    case 'bottom':
      return { side, top: spot.top + spot.height + gap, left: clampX(spot.left) }
    case 'top':
      return { side, top: spot.top - gap - card.height, left: clampX(spot.left) }
    case 'right':
      return { side, left: spot.left + spot.width + gap, top: clampY(spot.top) }
    case 'left':
      return { side, left: spot.left - gap - card.width, top: clampY(spot.top) }
    default:
      return { side: 'over', top: clampY(view.height - card.height - margin), left: clampX(view.width - card.width - margin) }
  }
}

// What the assistant is told

export interface TourMoment {
  tour: string
  title: string
  step: number
  total: number
  stepTitle: string
  body: string
}

/** The line the assistant's page context carries while a tour runs: which tour, which step, and what the step says. */
export function tourLine(m: TourMoment): string {
  return `Guided tour "${m.title}" (${m.tour}) is open, step ${m.step} of ${m.total}: "${m.stepTitle}". The step says: ${m.body}`
}

/** What `start_tour` answers: the tour, where it starts and its steps (titles and pages). */
export function describeTour(id: string, title: string, steps: Array<{ title: string; path: string }>, startsAt: number) {
  return { tour: id, title, startsAtStep: startsAt + 1, steps: steps.map((s, i) => ({ step: i + 1, title: s.title, page: s.path })) }
}

/** The error of `start_tour` for an id it does not know. */
export const unknownTour = (id: string, ids: string[]): string => `There is no tour "${id}". The tours are: ${ids.join(', ')}.`
