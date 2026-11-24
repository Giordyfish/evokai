import { meaningfulWords, stripEcho, tailWords } from '../src/browser/speech'
import { ingestAll, setup } from './helpers/voice'

const LONG = 'This week the shop refunded four thousand euros of orders, less than last week.'

function speaking(s: ReturnType<typeof setup>) {
  ingestAll(s.session, [
    { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
    { type: 'say', index: 0, text: LONG, turnId: 't1', seq: 2 },
    { type: 'say', index: 1, text: 'Shall I show the chart?', turnId: 't1', seq: 3 },
  ])
}

afterEach(() => {
  vi.useRealTimers()
})

describe('speech helpers', () => {
  test('hesitations are not words; single letters neither', () => {
    expect(meaningfulWords('uh, um, wait a second')).toEqual(['wait', 'second'])
    expect(meaningfulWords('ehm allora aspetta', 'it')).toEqual(['aspetta'])
  })

  test('the agent words at the start of a transcript are stripped', () => {
    expect(stripEcho('four thousand euros wait show me returns', LONG)).toBe('wait show me returns')
    expect(stripEcho('refunds 4000 stop', LONG)).toBe('stop') // stems and digits count as the agent's
    expect(tailWords('one two three four five six')).toBe('three four five six')
  })
})

describe('hold and resume', () => {
  test('a word holds the agent; a sound that never becomes a phrase resumes from the word reached', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { holdMs: 1000, settleMs: 0 } })
    await s.engine.start()
    const holds: boolean[] = []
    s.engine.on('hold', (h) => holds.push(h))
    speaking(s)
    expect(s.tts.spoken).toEqual([LONG])
    vi.advanceTimersByTime(1500) // about 21 characters spoken at rate 1
    s.rec.instances[0]!.say('wait please', false)
    expect(s.engine.held).toBe(true)
    expect(s.tts.synth.cancelled).toBeGreaterThanOrEqual(1)
    vi.advanceTimersByTime(1100)
    expect(s.engine.held).toBe(false)
    expect(holds).toEqual([true, false])
    const resumed = s.tts.spoken.slice(1)
    expect(resumed[0]).not.toBe(LONG)
    expect(LONG.endsWith(resumed[0]!)).toBe(true)
    s.tts.synth.finish()
    expect(s.tts.spoken.at(-1)).toBe('Shall I show the chart?')
    expect(s.m.calls).toHaveLength(0)
  })

  test('while held the agent is not speaking: the phase says it was interrupted, and speaking again clears it', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { holdMs: 1000, settleMs: 0 } })
    await s.engine.start()
    speaking(s)
    expect(s.session.state.phase).toBe('speaking')
    vi.advanceTimersByTime(1500)
    s.rec.instances[0]!.say('wait please', false)
    expect(s.engine.held).toBe(true)
    // the user is talking over it: listening, with the hint that the agent was cut off (not «speaking»)
    expect(s.session.state.phase).toBe('interrupted')
    vi.advanceTimersByTime(1100)
    expect(s.engine.held).toBe(false)
    expect(s.session.state.phase).toBe('speaking')
  })

  test('a held phrase that settles is sent, and its final result adds only the new words', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { settleMs: 500 } })
    await s.engine.start()
    speaking(s)
    const r = s.rec.instances[0]!
    r.say('which order', false)
    expect(s.engine.held).toBe(true)
    vi.advanceTimersByTime(600)
    await vi.waitFor(() => expect(s.m.calls.filter((c) => c.url.endsWith('/chat'))).toHaveLength(1))
    expect(s.m.calls.find((c) => c.url.endsWith('/chat'))!.body).toMatchObject({ text: 'which order', mode: 'voice' })
    r.say('which order exactly', true)
    await vi.waitFor(() => expect(s.m.calls.filter((c) => c.url.endsWith('/chat'))).toHaveLength(2))
    expect(s.m.calls.filter((c) => c.url.endsWith('/chat'))[1]!.body).toMatchObject({ text: 'exactly' })
  })

  test('the first meaningful word is enough to quiet it; a hesitation or its own words are not', async () => {
    const s = setup()
    await s.engine.start()
    speaking(s)
    s.rec.instances[0]!.say('um', false)
    expect(s.engine.held).toBe(false)
    s.rec.instances[0]!.say('refunded', false)
    expect(s.engine.held).toBe(false)
    s.rec.instances[0]!.say('refunded wait', false)
    expect(s.engine.held).toBe(true)
    expect(s.tts.synth.cancelled).toBeGreaterThanOrEqual(1)
  })

  test('hesitations and echo while the agent speaks do not stop it', async () => {
    const s = setup()
    await s.engine.start()
    speaking(s)
    s.rec.instances[0]!.say('uh um', false)
    s.rec.instances[0]!.say('four thousand euros', true)
    expect(s.engine.held).toBe(false)
    expect(s.engine.phase).toBe('speaking')
    expect(s.m.calls).toHaveLength(0)
  })
})

describe('short replies just after the agent has spoken', () => {
  async function spoken() {
    const s = setup()
    await s.engine.start()
    speaking(s)
    s.session.ingest({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 't1', seq: 4 })
    s.tts.synth.finish()
    s.tts.synth.finish()
    await vi.waitFor(() => expect(s.engine.phase).not.toBe('speaking'))
    return s
  }

  test('a reply of one or two of its words is the user and is sent («the chart», «orders»)', async () => {
    const s = await spoken()
    s.rec.instances.at(-1)!.say('the chart', true)
    await vi.waitFor(() => expect(s.m.calls).toHaveLength(1))
    s.rec.instances.at(-1)!.say('orders', true)
    await vi.waitFor(() => expect(s.m.calls).toHaveLength(2))
  })

  test('a late transcript of its own phrase is still echo and is not sent', async () => {
    const s = await spoken()
    s.rec.instances.at(-1)!.say('less than last week', true)
    await new Promise((r) => setTimeout(r, 20))
    expect(s.m.calls).toHaveLength(0)
  })
})

describe('strict turns (talking over the agent off)', () => {
  test('the microphone closes for the agent turn and opens again when it has spoken', async () => {
    const s = setup({ engine: { talkOver: false } })
    await s.engine.start()
    const mic: boolean[] = []
    s.engine.on('mic', (m) => mic.push(m))
    expect(s.engine.micOpen).toBe(true)
    speaking(s)
    expect(s.engine.micOpen).toBe(false)
    // Nothing heard can interrupt: the recogniser is closed.
    expect(s.rec.instances.at(-1)!.started).toBe(false)
    s.session.ingest({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 't1', seq: 4 })
    s.tts.synth.finish()
    s.tts.synth.finish()
    await vi.waitFor(() => expect(s.engine.micOpen).toBe(true))
    expect(mic).toEqual([false, true])
    expect(s.rec.instances.at(-1)!.started).toBe(true)
  })

  test('switching it on during the agent turn opens the microphone at once', async () => {
    const s = setup({ engine: { talkOver: false } })
    await s.engine.start()
    speaking(s)
    expect(s.engine.micOpen).toBe(false)
    s.engine.setTalkOver(true)
    await vi.waitFor(() => expect(s.engine.micOpen).toBe(true))
    expect(s.engine.talkOver).toBe(true)
    s.engine.setTalkOver(false)
    expect(s.engine.micOpen).toBe(false)
  })
})
