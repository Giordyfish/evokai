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
    expect(ids).toEqual(['first-steps', 'security', 'usage-traces'])
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
  ],
}

describe('the steps a person gets', () => {
  it('drop what needs an application, the right to create, or the deployment', () => {
    expect(needsOf(TOUR.steps[2]!)).toEqual(['app'])
    expect(stepsFor(TOUR, { app: 'shop', canCreate: true, owner: true }).map((s) => s.id)).toEqual(['a', 'b', 'c', 'd', 'e'])
    expect(stepsFor(TOUR, { app: 'shop', canCreate: false, owner: false }).map((s) => s.id)).toEqual(['a', 'c', 'e'])
    expect(stepsFor(TOUR, { app: null, canCreate: true, owner: false }).map((s) => s.id)).toEqual(['a', 'b'])
  })
  it('fill in the application', () => {
    const steps = stepsFor(TOUR, { app: 'shop', canCreate: false, owner: false })
    expect(steps.map((s) => s.path)).toEqual(['/apps', '/apps/shop/security', '/playground?app=shop'])
    expect(fillRoute('/apps/{app}', 'a b')).toBe('/apps/a%20b')
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
    expect(savedStep('a', { a: 2 }, { a: 4 })).toBe(2)
    expect(savedStep('a', {}, { a: 4 })).toBe(4)
    expect(savedStep('a', {}, {})).toBeUndefined()
  })
  it('keeps in the preferences only what the service accepts', () => {
    expect(sanitizeTours({ 'first-steps': 3, Bad: 1, 'x-y': -1, ok: 100, half: 1.5, security: 0 })).toEqual({ 'first-steps': 3, security: 0 })
    expect(sanitizeTours(null)).toEqual({})
    expect(sanitizeTours([1, 2])).toEqual({})
    expect(Object.keys(sanitizeTours(Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`tour-${i}`, 1]))))).toHaveLength(20)
    expect(DEFAULTS.tours).toEqual({})
    expect(DEFAULTS.tourInvite).toBe('show')
    expect(sanitize({ ...DEFAULTS, tourInvite: 'later' }).tourInvite).toBe('show')
    expect(sanitize({ ...DEFAULTS, tourInvite: 'dismissed', tours: { security: 2 } })).toMatchObject({ tourInvite: 'dismissed', tours: { security: 2 } })
  })
})

describe('the card', () => {
  const view = { width: 1200, height: 800 }
  const card = { width: 360, height: 180 }
  it('goes below the place, else above, right or left', () => {
    expect(placeCard({ top: 100, left: 200, width: 300, height: 100 }, card, view)).toEqual({ side: 'bottom', top: 214, left: 200 })
    expect(placeCard({ top: 560, left: 200, width: 300, height: 100 }, card, view)).toEqual({ side: 'top', top: 366, left: 200 })
    expect(placeCard({ top: 100, left: 100, width: 300, height: 650 }, card, view).side).toBe('right')
    expect(placeCard({ top: 100, left: 700, width: 480, height: 650 }, card, view).side).toBe('left')
  })
  it('takes the side asked for when it fits', () => {
    expect(placeCard({ top: 100, left: 200, width: 300, height: 100 }, card, view, 'right')).toEqual({ side: 'right', left: 514, top: 100 })
    expect(placeCard({ top: 100, left: 200, width: 300, height: 100 }, card, view, 'left').side).toBe('bottom')
  })
  it('stays inside the screen, over a place larger than it', () => {
    expect(placeCard({ top: 100, left: 1000, width: 150, height: 40 }, card, view)).toEqual({ side: 'bottom', top: 154, left: 824 })
    expect(placeCard({ top: 4, left: 4, width: 1190, height: 790 }, card, view)).toEqual({ side: 'over', top: 604, left: 824 })
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
