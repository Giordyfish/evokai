/**
 * The guided tours: every step points at a place its page has, every text exists in both languages, and the logic
 * (which steps, moving, resuming, skipping a place that never appears, placing the card, what the assistant is told).
 */
import APP_YAML from '../../src/evokai/assistant/app.yaml?raw'
import { pageContext } from '../src/assistant/launcher'
import { has } from '../src/i18n'
import { en } from '../src/i18n/en'
import { it as italian } from '../src/i18n/it'
import { sanitize, sanitizeTours, DEFAULTS } from '../src/prefs'
import {
  atRoute,
  describeTour,
  fillRoute,
  findStep,
  moveTo,
  needsOf,
  placeCard,
  resumeAt,
  savedStep,
  spotlight,
  statusOf,
  stepsFor,
  tourApp,
  tourGateway,
  type TourEnv,
  tourLine,
  unknownTour,
  waitFor,
} from '../src/tours/engine'
import { FIRST_TOUR, type Tour, tourById, TOURS } from '../src/tours/tours'

const SOURCES = import.meta.glob('../src/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const src = (path: string): string => {
  const s = SOURCES[`../src/${path}`]
  if (s == null) throw new Error(`no source ${path}`)
  return s
}

/** The page component each console view and application tab shows (checked against the shell's switch below). */
const VIEW_PAGE: Record<string, string> = { apps: 'Apps', setup: 'Setup', playground: 'Playground', usage: 'AllUsage', policy: 'SystemPolicy' }
const TAB_PAGE: Record<string, (id: string | undefined) => string> = {
  agents: (id) => (id ? 'AgentEditor' : 'Agents'),
  security: () => 'Security',
  usage: () => 'AppUsage',
  traces: () => 'Traces',
}
/** The file of a page component. */
const FILE: Record<string, string> = { AllUsage: 'Usage', AppUsage: 'Usage' }

function pageOf(route: string): string {
  const p = route.split('?')[0]!.replace(/^\//, '').split('/')
  // Every gateway page, its list and each of its tabs, is drawn by pages/Gateways.tsx.
  if (p[0] === 'gateways') return 'Gateways'
  if (p[0] === 'apps' && p[1]) {
    const tab = TAB_PAGE[p[2] ?? 'overview']
    if (!tab) throw new Error(`no page known for ${route}`)
    return tab(p[3])
  }
  const page = VIEW_PAGE[p[0]!]
  if (!page) throw new Error(`no page known for ${route}`)
  return page
}

/** A page's source and the sources of the page modules it imports, transitively. */
function sourcesOf(component: string): string[] {
  const seen = new Set<string>()
  const visit = (name: string) => {
    if (seen.has(name)) return
    seen.add(name)
    for (const m of src(`pages/${name}.tsx`).matchAll(/from '\.\/(\w+)'/g)) if (SOURCES[`../src/pages/${m[1]}.tsx`] != null) visit(m[1]!)
  }
  visit(FILE[component] ?? component)
  return [...seen].map((n) => src(`pages/${n}.tsx`))
}

const marks = (s: string, place: string): boolean => s.includes(`data-tour="${place}"`) || s.includes(`tour="${place}"`)

describe('the tours', () => {
  it('route every step to a page that marks its place', () => {
    const missing: string[] = []
    for (const tour of TOURS)
      for (const step of tour.steps) {
        const page = pageOf(step.route)
        if (!sourcesOf(page).some((s) => marks(s, step.target))) missing.push(`${tour.id}/${step.id}: ${step.target} not on ${page}`)
      }
    expect(missing).toEqual([])
  })

  it('know the pages the shell shows for those routes', () => {
    const shell = src('shell.tsx')
    for (const [view, page] of Object.entries(VIEW_PAGE)) expect(shell).toMatch(new RegExp(`case '${view}':\\s*return <${page}\\b`))
    expect(shell).toMatch(/case 'agents':\s*return id \? <AgentEditor[^:]*: <Agents\b/)
    expect(shell).toMatch(/case 'security':\s*return <Security\b/)
    expect(shell).toMatch(/case 'usage':\s*return <AppUsage\b/)
    expect(shell).toMatch(/case 'traces':\s*return <Traces\b/)
    expect(shell).toMatch(/route\.view === 'gateways'\) return <Gateways\b/)
  })

  it('have every text in English and Italian, two short lines at most', () => {
    const keys = TOURS.flatMap((tour) => [
      `tour.${tour.id}.title`,
      `tour.${tour.id}.sub`,
      ...tour.steps.flatMap((s) => [`tour.${tour.id}.${s.id}.title`, `tour.${tour.id}.${s.id}.body`]),
    ])
    expect(keys.filter((k) => !has(k))).toEqual([])
    expect(keys.filter((k) => !(k in italian))).toEqual([])
    const EN = en as unknown as Record<string, string>
    const IT = italian as unknown as Record<string, string>
    const long = keys.filter((k) => k.endsWith('.body') && (EN[k]!.length > 160 || IT[k]!.length > 170))
    expect(long).toEqual([])
  })

  it('have unique ids and the first tour the invite opens', () => {
    const ids = TOURS.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const t of TOURS) expect(new Set(t.steps.map((s) => s.id)).size).toBe(t.steps.length)
    expect(tourById(FIRST_TOUR)).toBeTruthy()
    expect(ids).toEqual(['first-steps', 'security', 'usage-traces', 'gateway'])
    for (const id of ids) expect(id).toMatch(/^[a-z][a-z0-9-]{1,39}$/)
  })
})

const TOUR: Tour = {
  id: 'demo',
  steps: [
    { id: 'a', route: '/apps', target: 'x' },
    { id: 'b', route: '/setup', target: 'y', needs: ['create'] },
    { id: 'c', route: '/apps/{app}/security', target: 'z' },
    { id: 'd', route: '/policy', target: 'w', needs: ['owner'] },
    { id: 'e', route: '/playground?app={app}', target: 'v' },
    { id: 'f', route: '/gateways', target: 'u', needs: ['createGateway'] },
    { id: 'g', route: '/gateways/{gateway}/usage', target: 't' },
  ],
}
const env = (e: Partial<TourEnv>): TourEnv => ({ app: null, canCreate: false, owner: false, gateway: null, canCreateGateway: false, ...e })

describe('the steps a person gets', () => {
  it('drop what needs an application, the right to create, or the deployment', () => {
    expect(needsOf(TOUR.steps[2]!)).toEqual(['app'])
    expect(needsOf(TOUR.steps[6]!)).toEqual(['gateway'])
    expect(stepsFor(TOUR, env({ app: 'shop', canCreate: true, owner: true })).map((s) => s.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(stepsFor(TOUR, env({ app: 'shop' })).map((s) => s.id)).toEqual(['a', 'c', 'e'])
    expect(stepsFor(TOUR, env({ canCreate: true })).map((s) => s.id)).toEqual(['a', 'b'])
  })
  it('drop what needs a gateway, or the right to create one', () => {
    expect(stepsFor(TOUR, env({ canCreateGateway: true })).map((s) => s.id)).toEqual(['a', 'f'])
    expect(stepsFor(TOUR, env({ gateway: 'devs' })).map((s) => s.id)).toEqual(['a', 'g'])
  })
  it('fill in the application and the gateway', () => {
    const steps = stepsFor(TOUR, env({ app: 'shop', gateway: 'devs' }))
    expect(steps.map((s) => s.path)).toEqual(['/apps', '/apps/shop/security', '/playground?app=shop', '/gateways/devs/usage'])
    expect(fillRoute('/apps/{app}', 'a b')).toBe('/apps/a%20b')
    expect(tourGateway('#/gateways/devs/policy', ['ops', 'devs'])).toBe('devs')
    expect(tourGateway('#/gateways', ['ops', 'devs'])).toBe('ops')
    expect(tourGateway('#/apps/devs', [])).toBeNull()
  })
  it('show the open application, else the first', () => {
    expect(tourApp('#/apps/books/usage', ['shop', 'books'])).toBe('books')
    expect(tourApp('#/apps/gone/usage', ['shop', 'books'])).toBe('shop')
    expect(tourApp('#/usage', ['shop'])).toBe('shop')
    expect(tourApp('#/usage', [])).toBeNull()
  })
  it('stay on a page that is already the route', () => {
    expect(atRoute('#/apps/shop/security', '/apps/shop/security')).toBe(true)
    expect(atRoute('#/apps/shop/security/', '/apps/shop/security')).toBe(true)
    expect(atRoute('#/apps/shop', '/apps/shop/security')).toBe(false)
    expect(atRoute('#/playground?app=shop&agent=help', '/playground?app=shop')).toBe(true)
    expect(atRoute('#/playground?app=other', '/playground?app=shop')).toBe(false)
    expect(atRoute('', '/apps')).toBe(false)
  })
})

describe('moving', () => {
  it('goes forward and back, finishes past the last, stays before the first', () => {
    expect(moveTo(0, 1, 3)).toBe(1)
    expect(moveTo(2, -1, 3)).toBe(1)
    expect(moveTo(2, 1, 3)).toBe('done')
    expect(moveTo(0, -1, 3)).toBeNull()
  })
  it('skips a step whose place never appears, in either direction', async () => {
    const there = new Set([0, 3])
    const asked: number[] = []
    const locate = async (i: number) => {
      asked.push(i)
      return there.has(i)
    }
    expect(await findStep(5, 1, 1, locate)).toEqual({ index: 3, skipped: [1, 2] })
    expect(asked).toEqual([1, 2, 3])
    expect(await findStep(5, 2, -1, locate)).toEqual({ index: 0, skipped: [2, 1] })
    expect(await findStep(5, 4, 1, locate)).toEqual({ index: null, skipped: [4] })
    expect(await findStep(5, 0, 1, async () => false)).toEqual({ index: null, skipped: [0, 1, 2, 3, 4] })
  })
})

describe('waiting for a place', () => {
  const clock = () => {
    let now = 0
    return { now: () => now, sleep: async (ms: number) => void (now += ms) }
  }
  it('answers as soon as it appears', async () => {
    let calls = 0
    const found = await waitFor(() => (++calls === 4 ? 'here' : null), 1000, 100, clock())
    expect(found).toBe('here')
    expect(calls).toBe(4)
  })
  it('gives up after the time instead of hanging', async () => {
    let calls = 0
    const found = await waitFor(
      () => {
        calls++
        return null
      },
      1000,
      100,
      clock(),
    )
    expect(found).toBeNull()
    expect(calls).toBe(11)
  })
})

describe('resuming', () => {
  it('starts where the tour stopped, or at the first step', () => {
    expect(resumeAt(undefined, 5)).toBe(0)
    expect(resumeAt(3, 5)).toBe(3)
    expect(resumeAt(5, 5)).toBe(0)
    expect(resumeAt(9, 5)).toBe(0)
    expect(resumeAt(-1, 5)).toBe(0)
    expect(resumeAt(1.5, 5)).toBe(0)
  })
  it('says where each tour stands', () => {
    expect(statusOf(undefined, 5)).toEqual({ kind: 'new' })
    expect(statusOf(0, 5)).toEqual({ kind: 'new' })
    expect(statusOf(2, 5)).toEqual({ kind: 'stopped', step: 3 })
    expect(statusOf(5, 5)).toEqual({ kind: 'done' })
  })
  it("reads this browser's copy first, then the preferences", () => {
    // The most recent copy wins: this browser's (every step) or the preferences' (another browser, on leaving).
    expect(savedStep('a', { a: { step: 2, at: 200 } }, { a: { step: 4, at: 100 } })).toBe(2)
    expect(savedStep('a', { a: { step: 2, at: 100 } }, { a: { step: 4, at: 200 } })).toBe(4)
    expect(savedStep('a', {}, { a: { step: 4, at: 1 } })).toBe(4)
    expect(savedStep('a', { a: { step: 1, at: 1 } }, {})).toBe(1)
    expect(savedStep('a', {}, {})).toBeUndefined()
  })
  it('keeps in the preferences only what the service accepts', () => {
    const p = (step: unknown, at: unknown = 5) => ({ step, at })
    expect(
      sanitizeTours({ 'first-steps': p(3), Bad: p(1), 'x-y': p(-1), ok: p(100), half: p(1.5), old: 2, noat: { step: 1 }, security: p(0, 0) }),
    ).toEqual({ 'first-steps': { step: 3, at: 5 }, security: { step: 0, at: 0 } })
    expect(sanitizeTours({ a1: { step: 1, at: 2, extra: true } })).toEqual({ a1: { step: 1, at: 2 } })
    expect(sanitizeTours(null)).toEqual({})
    expect(sanitizeTours([1, 2])).toEqual({})
    expect(Object.keys(sanitizeTours(Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`tour-${i}`, p(1)]))))).toHaveLength(20)
    expect(DEFAULTS.tours).toEqual({})
    expect(DEFAULTS.tourInvite).toBe('show')
    expect(sanitize({ ...DEFAULTS, tourInvite: 'later' }).tourInvite).toBe('show')
    expect(sanitize({ ...DEFAULTS, tourInvite: 'dismissed', tours: { security: { step: 2, at: 9 } } })).toMatchObject({
      tourInvite: 'dismissed',
      tours: { security: { step: 2, at: 9 } },
    })
  })
})

describe('the card', () => {
  const view = { width: 1200, height: 800 }
  const card = { width: 360, height: 180 }
  const small = { top: 100, left: 200, width: 300, height: 100 }
  it('goes below the place, else above, right or left', () => {
    expect(placeCard(small, card, view)).toEqual({ side: 'bottom', top: 214, left: 200, spot: small })
    expect(placeCard({ ...small, top: 560 }, card, view)).toMatchObject({ side: 'top', top: 366, left: 200 })
    expect(placeCard({ top: 100, left: 100, width: 300, height: 650 }, card, view).side).toBe('right')
    expect(placeCard({ top: 100, left: 700, width: 480, height: 650 }, card, view).side).toBe('left')
  })
  it('takes the side asked for when it fits', () => {
    expect(placeCard(small, card, view, 'right')).toMatchObject({ side: 'right', left: 514, top: 100 })
    expect(placeCard(small, card, view, 'left').side).toBe('bottom')
  })
  it('stays inside the screen', () => {
    expect(placeCard({ top: 100, left: 1000, width: 150, height: 40 }, card, view)).toMatchObject({ side: 'bottom', top: 154, left: 824 })
  })
  it('never covers a place larger than the screen: the light stops above the card', () => {
    const tall = { top: 66, left: 290, width: 1100, height: 830 }
    const p = placeCard(tall, card, view)
    expect(p).toEqual({ side: 'inside', top: 604, left: 290, spot: { ...tall, height: 604 - 14 - 66 } })
    expect(p.spot.top + p.spot.height).toBeLessThan(p.top)
  })
  it('keeps out of the room the assistant takes', () => {
    // The assistant's panel from x = 800: the card is placed in the 792 pixels left of it.
    const p = placeCard({ top: 100, left: 300, width: 600, height: 100 }, card, { width: 792, height: 800 })
    expect(p.left + card.width).toBeLessThanOrEqual(792 - 16)
  })
  it('lights the place with room around it, inside the screen', () => {
    expect(spotlight({ top: 100, left: 200, width: 300, height: 100 }, view)).toEqual({ top: 94, left: 194, width: 312, height: 112 })
    expect(spotlight({ top: -50, left: 0, width: 1300, height: 2000 }, view)).toEqual({ top: 4, left: 4, width: 1192, height: 792 })
  })
})

describe('the assistant', () => {
  it("names every tour in its start_tour tool", () => {
    const tool = APP_YAML.slice(APP_YAML.indexOf('- name: start_tour'))
    expect(tool).toContain('wait: true')
    for (const t of TOURS) expect(tool).toContain(t.id)
  })
  it('is told the tour and the step while one runs', () => {
    const line = tourLine({ tour: 'security', title: 'Security', step: 3, total: 5, stepTitle: 'Versions', body: 'Every save is a version.' })
    expect(line).toBe('Guided tour "Security" (security) is open, step 3 of 5: "Versions". The step says: Every save is a version.')
    expect(pageContext('#/apps/shop/security', 'Security', line)).toEqual({ path: '/apps/shop/security', title: 'Security', text: line })
    expect(pageContext('#/apps', 'Applications', null)).toEqual({ path: '/apps', title: 'Applications' })
  })
  it('gets the steps of the tour it starts, or the tour ids', () => {
    expect(describeTour('demo', 'Demo', [{ title: 'A', path: '/apps' }, { title: 'B', path: '/usage' }], 1)).toEqual({
      tour: 'demo',
      title: 'Demo',
      startsAtStep: 2,
      steps: [
        { step: 1, title: 'A', page: '/apps' },
        { step: 2, title: 'B', page: '/usage' },
      ],
    })
    expect(unknownTour('nope', ['first-steps', 'security'])).toBe('There is no tour "nope". The tours are: first-steps, security.')
  })
})
