import { BOX_IN_MS, BOX_OUT_MS, Flow, HOP_MS, hopAt, restOf, settled, viewOf, type FlowIO, type Look, type Rest, type Spot, type View } from '../src/flow'
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

describe('the sequencer: the orb never crosses the page, it fades out where it was and in where it goes', () => {
  test('open the chat: the box is measured unseen, the launcher orb hops to the header while the box rises', async () => {
    const { events } = await run('closed', 'chat')
    expect(events[flyIndex(events)]).toMatchObject({ kind: 'fly', from: 'launcher', to: 'head', ms: HOP_MS })
    // The place it goes is measured on the box laid out unseen, before the hop.
    expect(boxIndex(events, 'pre')).toBeLessThan(flyIndex(events))
    expect(flyIndex(events)).toBeLessThan(boxIndex(events, 'in'))
    expect(events).toContainEqual({ kind: 'wait', ms: BOX_IN_MS })
    // While the box rises the orb is between places: it rests in the header only once the box is open.
    expect(shows(events).find((l) => l.box === 'in')?.orb).toBe('flying')
    expect(shows(events).at(-1)).toEqual(settled('chat'))
  })

  test('close the chat: the box sinks away while the orb hops down to the launcher', async () => {
    const { events } = await run('chat', 'closed')
    expect(boxIndex(events, 'out')).toBeLessThan(flyIndex(events))
    expect(shows(events)[0]).toMatchObject({ box: 'out', orb: 'flying' })
    expect(events[flyIndex(events)]).toMatchObject({ from: 'head', to: 'launcher', ms: HOP_MS })
    expect(events).toContainEqual({ kind: 'wait', ms: BOX_OUT_MS })
    expect(shows(events).at(-1)).toEqual(settled('closed'))
  })

  test('close from voice: the orb hops from the middle to the launcher', async () => {
    const { events } = await run('voice', 'closed')
    expect(events[flyIndex(events)]).toMatchObject({ from: 'center', to: 'launcher' })
    expect(shows(events).some((l) => l.layer === 'chat' && l.box !== 'none')).toBe(false)
  })

  test('minimize: the box sinks away, the orb hops into the launcher, then the bubble appears', async () => {
    const { events } = await run('voice', 'minimized')
    const fly = flyIndex(events)
    expect(boxIndex(events, 'out')).toBeLessThan(fly)
    expect(events[fly]).toMatchObject({ from: 'center', to: 'launcher', ms: HOP_MS })
    expect(at(events, (e) => e.kind === 'show' && e.look.bubble)).toBeGreaterThan(fly)
    expect(shows(events).at(-1)).toEqual(settled('minimized'))
  })

  test('restore: the orb hops up to the middle while the box rises on voice mode', async () => {
    const { events } = await run('minimized', 'voice')
    const fly = flyIndex(events)
    expect(events[fly]).toMatchObject({ from: 'launcher', to: 'center' })
    expect(fly).toBeLessThan(boxIndex(events, 'in'))
    expect(shows(events)[0]).toMatchObject({ box: 'pre', layer: 'voice', bubble: false })
  })

  test('voice on and off inside the open panel: the orb hops between header and middle while the layers crossfade', async () => {
    const on = await run('chat', 'voice')
    expect(on.events[0]).toMatchObject({ kind: 'show', look: { box: 'open', layer: 'voice', orb: 'flying' } })
    expect(on.events[1]).toMatchObject({ kind: 'fly', from: 'head', to: 'center', ms: HOP_MS })
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

describe('the hop', () => {
  const a = SPOTS.launcher
  const b = SPOTS.center

  test('fades out and shrinks where it was, then grows and fades in where it goes, never in between', () => {
    expect(hopAt(a, b, 0)).toEqual({ spot: a, scale: 1, opacity: 1 })
    const ts = Array.from({ length: 41 }, (_, i) => i / 40)
    for (const t of ts) {
      const h = hopAt(a, b, t)
      // Only ever on one of the two places: nothing travels across the page.
      expect([a, b]).toContain(h.spot)
      expect(h.scale).toBeGreaterThanOrEqual(0.6)
      expect(h.scale).toBeLessThanOrEqual(1)
      // Seen on one place only while it is out of the other.
      if (h.spot === a) expect(h.opacity).toBeGreaterThan(0)
    }
    const out = ts.filter((t) => hopAt(a, b, t).spot === a)
    expect(Math.max(...out)).toBeLessThan(0.5)
    expect(hopAt(a, b, 0.45).opacity).toBe(0)
    expect(hopAt(a, b, 1)).toEqual({ spot: b, scale: 1, opacity: 1 })
  })

  test('the fade is monotonic on each side', () => {
    const op = (t: number) => hopAt(a, b, t).opacity
    for (let t = 0; t < 0.38; t += 0.02) expect(op(t + 0.02)).toBeLessThanOrEqual(op(t))
    for (let t = 0.5; t < 0.98; t += 0.02) expect(op(t + 0.02)).toBeGreaterThanOrEqual(op(t))
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
