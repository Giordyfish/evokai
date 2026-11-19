import { BOX_IN_MS, BOX_OUT_MS, cubicBezier, Flow, flightAt, restOf, settled, viewOf, type FlowIO, type Look, type Rest, type Spot, type View } from '../src/flow'
import { lineWindow } from '../src/panel'

const SPOTS: Record<Rest, Spot> = {
  launcher: { x: 1226, y: 746, size: 60 },
  head: { x: 890, y: 106, size: 42 },
  center: { x: 1052, y: 272, size: 168 },
}

type Ev = { kind: 'show'; look: Look } | { kind: 'fly'; from: Rest; to: Rest; ms: number } | { kind: 'wait'; ms: number }

/** A page that records what the sequencer asks of it, instantly. */
function recorder(motion = true) {
  const events: Ev[] = []
  const restAt = (s: Spot) => (Object.keys(SPOTS) as Rest[]).find((k) => SPOTS[k] === s) as Rest
  const io: FlowIO = {
    show: (look) => void events.push({ kind: 'show', look }),
    spot: (r) => SPOTS[r],
    fly: async (a, b, ms) => void events.push({ kind: 'fly', from: restAt(a), to: restAt(b), ms }),
    wait: async (ms) => void events.push({ kind: 'wait', ms }),
    motion: () => motion,
  }
  return { io, events }
}

async function run(from: View, to: View, motion = true) {
  const { io, events } = recorder(motion)
  const f = new Flow(from, io)
  f.go(to)
  await f.settledOn()
  return { f, events }
}

const shows = (events: Ev[]) => events.filter((e): e is Extract<Ev, { kind: 'show' }> => e.kind === 'show').map((e) => e.look)
const at = (events: Ev[], pred: (e: Ev) => boolean) => events.findIndex(pred)
const flyIndex = (events: Ev[]) => at(events, (e) => e.kind === 'fly')
const boxIndex = (events: Ev[], box: Look['box']) => at(events, (e) => e.kind === 'show' && e.look.box === box)

/** The one-orb rule, look by look: the orb is in one place, and only where that place is on screen. */
function oneOrb(l: Look): void {
  if (l.orb === 'launcher') expect(l.box).toBe('none')
  if (l.orb === 'head' || l.orb === 'center') expect(['in', 'open', 'out']).toContain(l.box)
  if (l.orb === 'head' && l.box === 'open') expect(l.layer).toBe('chat')
  if (l.orb === 'center' && l.box === 'open') expect(l.layer).toBe('voice')
  if (l.bubble) expect(l.orb).toBe('launcher')
}

describe('the views', () => {
  test('each view has its resting place and its look', () => {
    expect(restOf('closed')).toBe('launcher')
    expect(restOf('minimized')).toBe('launcher')
    expect(restOf('chat')).toBe('head')
    expect(restOf('voice')).toBe('center')
    expect(settled('chat')).toEqual({ view: 'chat', box: 'open', layer: 'chat', orb: 'head', bubble: false })
    expect(settled('voice')).toEqual({ view: 'voice', box: 'open', layer: 'voice', orb: 'center', bubble: false })
    expect(settled('minimized')).toEqual({ view: 'minimized', box: 'none', layer: 'chat', orb: 'launcher', bubble: true })
    expect(settled('closed')).toEqual({ view: 'closed', box: 'none', layer: 'chat', orb: 'launcher', bubble: false })
  })

  test('the view comes from the panel being open, voice being on and voice belonging to this panel', () => {
    expect(viewOf(false, false, false)).toBe('closed')
    expect(viewOf(true, false, false)).toBe('chat')
    expect(viewOf(true, true, true)).toBe('voice')
    expect(viewOf(false, true, true)).toBe('minimized')
    // Voice started elsewhere (a standalone <evokai-voice> on the same conversation) does not minimize into this panel.
    expect(viewOf(false, true, false)).toBe('closed')
  })
})

describe('the sequencer: the orb moves first when something opens, the box closes first when something closes', () => {
  test('open the chat: the orb flies from the launcher to the header, then the box opens around it', async () => {
    const { events } = await run('closed', 'chat')
    const fly = events[flyIndex(events)]
    expect(fly).toMatchObject({ kind: 'fly', from: 'launcher', to: 'head', ms: 700 })
    expect(boxIndex(events, 'pre')).toBeLessThan(flyIndex(events))
    expect(flyIndex(events)).toBeLessThan(boxIndex(events, 'in'))
    expect(events).toContainEqual({ kind: 'wait', ms: BOX_IN_MS })
    // The box is laid out unseen during the flight, never shown before the orb lands.
    const looks = shows(events)
    for (const l of looks.slice(0, looks.findIndex((x) => x.box === 'in'))) expect(['none', 'pre']).toContain(l.box)
    expect(shows(events).at(-1)).toEqual(settled('chat'))
  })

  test('close the chat: the box closes, then the orb flies down to the launcher', async () => {
    const { events } = await run('chat', 'closed')
    expect(boxIndex(events, 'out')).toBeLessThan(flyIndex(events))
    expect(events[flyIndex(events)]).toMatchObject({ from: 'head', to: 'launcher' })
    expect(at(events, (e) => e.kind === 'wait' && e.ms === BOX_OUT_MS)).toBeLessThan(flyIndex(events))
    expect(shows(events).at(-1)).toEqual(settled('closed'))
  })

  test('close from voice: the box closes, then the orb flies from the middle to the launcher', async () => {
    const { events } = await run('voice', 'closed')
    expect(boxIndex(events, 'out')).toBeLessThan(flyIndex(events))
    expect(events[flyIndex(events)]).toMatchObject({ from: 'center', to: 'launcher' })
    expect(shows(events).some((l) => l.layer === 'chat' && l.box !== 'none')).toBe(false)
  })

  test('minimize: the box closes, the orb flies into the launcher, then the bubble appears', async () => {
    const { events } = await run('voice', 'minimized')
    const fly = flyIndex(events)
    expect(boxIndex(events, 'out')).toBeLessThan(fly)
    expect(events[fly]).toMatchObject({ from: 'center', to: 'launcher', ms: 820 })
    const bubbleAt = at(events, (e) => e.kind === 'show' && e.look.bubble)
    expect(bubbleAt).toBeGreaterThan(fly)
    expect(shows(events).at(-1)).toEqual(settled('minimized'))
  })

  test('restore: the orb flies up to the middle, then the box opens around it on voice mode', async () => {
    const { events } = await run('minimized', 'voice')
    const fly = flyIndex(events)
    expect(events[fly]).toMatchObject({ from: 'launcher', to: 'center' })
    expect(fly).toBeLessThan(boxIndex(events, 'in'))
    expect(shows(events)[0]).toMatchObject({ box: 'pre', layer: 'voice', bubble: false })
  })

  test('voice on and off inside the open panel: header and middle swap while the layers crossfade', async () => {
    const on = await run('chat', 'voice')
    expect(on.events[0]).toMatchObject({ kind: 'show', look: { box: 'open', layer: 'voice', orb: 'flying' } })
    expect(on.events[1]).toMatchObject({ kind: 'fly', from: 'head', to: 'center', ms: 720 })
    expect(shows(on.events).every((l) => l.box === 'open')).toBe(true)
    const off = await run('voice', 'chat')
    expect(off.events[1]).toMatchObject({ kind: 'fly', from: 'center', to: 'head' })
  })

  test('ending voice from the minimized orb leaves the panel closed: nothing flies', async () => {
    const { events } = await run('minimized', 'closed')
    expect(flyIndex(events)).toBe(-1)
    expect(shows(events)).toEqual([settled('closed')])
  })

  test('without motion every change is instant', async () => {
    for (const [a, b] of [['closed', 'chat'], ['chat', 'voice'], ['voice', 'minimized'], ['minimized', 'voice'], ['voice', 'closed']] as Array<[View, View]>) {
      const { events } = await run(a, b, false)
      expect(events).toEqual([{ kind: 'show', look: settled(b) }])
    }
  })

  test('a change of mind during a transition ends on the last view asked for', async () => {
    const { io, events } = recorder()
    const f = new Flow('closed', io)
    f.go('chat')
    f.go('voice')
    await f.settledOn()
    expect(f.look).toEqual(settled('voice'))
    expect(f.moving).toBe(false)
    f.go('minimized')
    f.go('closed')
    await f.settledOn()
    expect(f.look).toEqual(settled('closed'))
    for (const l of shows(events)) oneOrb(l)
  })

  test('one orb at any time: every look of every transition keeps the orb in a single place on screen', async () => {
    const views: View[] = ['closed', 'chat', 'voice', 'minimized']
    for (const a of views)
      for (const b of views) {
        if (a === b) continue
        const { events } = await run(a, b)
        for (const l of shows(events)) oneOrb(l)
      }
  })
})

describe('the flight', () => {
  const a = SPOTS.launcher
  const b = SPOTS.center

  test('starts where it leaves and lands where it goes, at their sizes', () => {
    expect(flightAt(a, b, 0)).toEqual({ x: a.x, y: a.y, size: a.size })
    const end = flightAt(a, b, 1)
    expect(end.x).toBeCloseTo(b.x)
    expect(end.y).toBeCloseTo(b.y)
    expect(end.size).toBeCloseTo(b.size)
  })

  test('follows a slight arc and overshoots a little before landing', () => {
    // On the way, the orb leaves the straight line from the launcher up to the middle.
    const off = (t: number) => {
      const p = flightAt(a, b, t)
      return Math.abs((b.x - a.x) * (a.y - p.y) - (a.x - p.x) * (b.y - a.y)) / Math.hypot(b.x - a.x, b.y - a.y)
    }
    expect(Math.max(...Array.from({ length: 21 }, (_, i) => off(i / 20)))).toBeGreaterThan(10)
    const sizes = Array.from({ length: 41 }, (_, i) => flightAt(a, b, i / 40).size)
    expect(Math.max(...sizes)).toBeGreaterThan(b.size)
    expect(Math.max(...sizes)).toBeLessThan(b.size * 1.07)
  })

  test('the easing is a CSS cubic-bezier', () => {
    const linear = cubicBezier(0, 0, 1, 1)
    expect(linear(0.3)).toBeCloseTo(0.3, 3)
    const ease = cubicBezier(0.45, 0.05, 0.25, 1)
    expect(ease(0)).toBe(0)
    expect(ease(1)).toBe(1)
    expect(ease(0.5)).toBeGreaterThan(0.5)
  })
})

describe('the minimized bubble line', () => {
  test('keeps the end of what was said and the start of what follows, at word boundaries', () => {
    const text = 'Damaged in transit leads with twelve returns, the most of the three reasons. I highlighted it in the chart.'
    const cut = text.indexOf(' I highlighted')
    const w = lineWindow(text, cut)
    expect(text.slice(0, cut).endsWith(w.said)).toBe(true)
    expect(w.said.length).toBeLessThanOrEqual(46)
    expect(w.said.startsWith(' ')).toBe(false)
    expect(w.tosay.startsWith(' I highlighted')).toBe(true)
    expect(w.tosay.length).toBeLessThan(30)
    expect(lineWindow('Short.', 3)).toEqual({ said: 'Sho', tosay: 'rt.' })
  })
})
