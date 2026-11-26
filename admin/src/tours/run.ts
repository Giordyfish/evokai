/**
 * The tour that runs now, one at a time for the whole console: starting, moving, leaving and finishing it, where each
 * tour stopped, and the assistant's `start_tour` tool and page context. The overlay (`Tour.tsx`) draws what this holds.
 *
 * Progress is kept per tour: in this browser at every step (`localStorage`, when the browser allows it) and in the
 * person's console preferences when they leave or finish a tour (one save, so the audit log gets no entry per step).
 */
import { has, type TextKey, t } from '../i18n'
import { toast } from '../magic'
import { gateways as gatewayApi } from '../gateway'
import { prefs, sanitizeTours, setPrefs, type TourProgress } from '../prefs'
import { go } from '../state'
import {
  atRoute,
  describeTour,
  type Direction,
  findStep,
  moveTo,
  type ResolvedStep,
  resumeAt,
  savedStep,
  statusOf,
  stepsFor,
  type TourEnv,
  type TourStatus,
  tourApp,
  tourGateway,
  tourLine,
  unknownTour,
  waitFor,
} from './engine'
import { type Tour, tourById, TOURS } from './tours'

export interface TourRun {
  tour: Tour
  steps: ResolvedStep[]
  /** The step shown (or the last one shown while `moving`). */
  index: number
  /** Going to the next place: its page is opening, its place not found yet. */
  moving: boolean
  /** The element the step points at, once found. */
  target: HTMLElement | null
  /** How many steps the last move skipped because their place never appeared. */
  skipped: number
}

/** A text of a tour, by the end of its key (`title`, `sub`, `<step>.title`, `<step>.body`). */
export const tourText = (tour: string, rest: string): string => {
  const key = `tour.${tour}.${rest}`
  return has(key) ? t(key as TextKey) : key
}

// What decides the steps: the applications listed and the person's rights (set by the shell), and the gateways
// (read from the service when a tour starts or the Guides menu opens).

let env: { slugs: string[]; canCreate: boolean; owner: boolean } = { slugs: [], canCreate: false, owner: false }
let gw: { slugs: string[]; canCreate: boolean } = { slugs: [], canCreate: false }

export function setTourEnv(e: typeof env): void {
  env = e
}

/** Read the gateways this person sees (a failure counts as none). */
export async function refreshGateways(): Promise<void> {
  try {
    const l = await gatewayApi.list()
    gw = { slugs: l.items.map((g) => g.slug), canCreate: l.canCreate }
  } catch {
    gw = { slugs: [], canCreate: false }
  }
}

const envNow = (): TourEnv => ({
  app: tourApp(location.hash, env.slugs),
  canCreate: env.canCreate,
  owner: env.owner,
  gateway: tourGateway(location.hash, gw.slugs),
  canCreateGateway: gw.canCreate,
})

// Where each tour stopped.

const LOCAL = 'evk-tours'

function readLocal(): Record<string, TourProgress> {
  try {
    return sanitizeTours(JSON.parse(localStorage.getItem(LOCAL) ?? '{}'))
  } catch {
    return {}
  }
}

function writeLocal(id: string, p: TourProgress): void {
  try {
    localStorage.setItem(LOCAL, JSON.stringify(sanitizeTours({ ...readLocal(), [id]: p })))
  } catch {
    /* storage blocked: the preferences keep the step when the tour is left */
  }
}

function keep(id: string, step: number): void {
  const p = { step, at: Date.now() }
  writeLocal(id, p)
  setPrefs({ tours: { ...prefs().tours, [id]: p }, tourInvite: 'dismissed' })
}

/** The tours a person can take, with their steps and where they stand. */
export function guides(): Array<{ tour: Tour; steps: ResolvedStep[]; status: TourStatus }> {
  const local = readLocal()
  const e = envNow()
  return TOURS.map((tour) => {
    const steps = stepsFor(tour, e)
    return { tour, steps, status: statusOf(savedStep(tour.id, local, prefs().tours), steps.length) }
  }).filter((g) => g.steps.length)
}

// The run.

let run: TourRun | null = null
let seq = 0
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((f) => f())

export const tourRun = (): TourRun | null => run

export function onTour(f: () => void): () => void {
  listeners.add(f)
  return () => listeners.delete(f)
}

/** The first element marked with a place that is on screen (a hidden twin, such as an empty state's, does not count). */
function placeOf(name: string): HTMLElement | null {
  for (const el of document.querySelectorAll<HTMLElement>(`[data-tour="${CSS.escape(name)}"]`)) if (el.getClientRects().length) return el
  return null
}

async function show(from: number, dir: Direction): Promise<void> {
  if (!run) return
  const my = ++seq
  // A new object each change: the overlay re-renders on identity.
  const r: TourRun = { ...run, moving: true }
  run = r
  emit()
  const hit: { el: HTMLElement | null } = { el: null }
  const found = await findStep(r.steps.length, from, dir, async (i) => {
    if (my !== seq) return true
    const s = r.steps[i]!
    if (!atRoute(location.hash, s.path)) go(`#${s.path}`)
    const got = await waitFor<HTMLElement | 'gone'>(() => (my !== seq ? 'gone' : placeOf(s.target)))
    if (got === 'gone') return true
    hit.el = got
    return !!got
  })
  if (my !== seq || run !== r) return
  const el = hit.el
  if (found.index == null || !el) {
    if (dir === -1) return void show(r.index, 1)
    if (!r.target) {
      end()
      toast(t('tour.nothing'), t('tour.nothingSub'), null)
      return
    }
    return finish()
  }
  run = { ...r, index: found.index, target: el, moving: false, skipped: found.skipped.length }
  writeLocal(r.tour.id, { step: found.index, at: Date.now() })
  emit()
}

function end(): void {
  seq++
  run = null
  emit()
}

/**
 * Start a tour, where the person left it (or at its first step with `restart`).
 *
 * @returns The tour's steps and where it starts.
 * @throws When there is no such tour, or it has no step for this person.
 */
export async function startTour(id: string, opts: { restart?: boolean } = {}): Promise<ReturnType<typeof describeTour>> {
  const tour = tourById(id)
  if (!tour) throw new Error(unknownTour(id, TOURS.map((x) => x.id)))
  if (tour.steps.some((s) => s.route.includes('{gateway}') || s.needs?.includes('createGateway'))) await refreshGateways()
  const steps = stepsFor(tour, envNow())
  if (!steps.length) throw new Error(`The tour "${id}" has no step this person can see: its pages need an application or a role they do not have.`)
  const at = opts.restart ? 0 : resumeAt(savedStep(id, readLocal(), prefs().tours), steps.length)
  if (prefs().tourInvite === 'show') setPrefs({ tourInvite: 'dismissed' })
  seq++
  run = { tour, steps, index: at, moving: true, target: null, skipped: 0 }
  void show(at, 1)
  return describeTour(
    id,
    tourText(id, 'title'),
    steps.map((s) => ({ title: tourText(id, `${s.id}.title`), path: s.path })),
    at,
  )
}

/** Start a tour from the console (the Guides menu, the invite): a tour with nothing to show says so. */
export async function openTour(id: string, restart = false): Promise<void> {
  try {
    await startTour(id, { restart })
  } catch {
    toast(t('tour.nothing'), t('tour.nothingSub'), null)
  }
}

/** Next or Back. Past the last step the tour is finished; Back on the first stays. */
export function moveTour(dir: Direction): void {
  const r = run
  if (!r || r.moving) return
  const to = moveTo(r.index, dir, r.steps.length)
  if (to === 'done') return finish()
  if (to == null) return
  void show(to, dir)
}

/** Leave the tour: it resumes at this step next time. */
export function exitTour(): void {
  const r = run
  if (!r) return
  end()
  keep(r.tour.id, r.index)
}

function finish(): void {
  const r = run
  if (!r) return
  end()
  keep(r.tour.id, r.steps.length)
  toast(t('tour.ended'), t('tour.endedSub'), null)
}

/** The assistant's page context while a tour runs: the tour, the step and what it says. */
export function tourContext(): string | null {
  const r = run
  if (!r) return null
  const s = r.steps[r.index]!
  return tourLine({
    tour: r.tour.id,
    title: tourText(r.tour.id, 'title'),
    step: r.index + 1,
    total: r.steps.length,
    stepTitle: tourText(r.tour.id, `${s.id}.title`),
    body: tourText(r.tour.id, `${s.id}.body`),
  })
}

/** The assistant's `start_tour` client tool: start the tour asked for, in front of the person. */
export function startTourTool(args: { tour?: unknown; restart?: unknown }): Promise<ReturnType<typeof describeTour>> {
  const id = typeof args.tour === 'string' ? args.tour.trim() : ''
  return startTour(id, { restart: args.restart === true })
}
