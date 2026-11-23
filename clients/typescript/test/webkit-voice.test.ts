/**
 * The browser voice engine against recognisers that behave like WebKit's (Safari, every iOS browser): one that goes
 * deaf after the agent's audio without ending, one that ends with its last phrase still interim, results that repeat
 * the earlier phrases, restarts that fail outside a user gesture, and a short reply just after the agent spoke.
 */
import type { RecognitionResultEventLike } from '../src/browser/stt/on-device'
import { OnDeviceStt, withoutRepeat } from '../src/browser/stt/on-device'
import { fakeRecognition, ingestAll, setup } from './helpers/voice'

const chats = (s: ReturnType<typeof setup>) => s.m.calls.filter((c) => c.url.endsWith('/chat')).map((c) => (c.body as { text: string }).text)

/** A WebKit result list: the finals so far, then the phrase being heard; `resultIndex` is the first that changed. */
function results(finals: string[], current?: { text: string; isFinal: boolean }): RecognitionResultEventLike {
  const list = [...finals.map((t) => Object.assign([{ transcript: t }], { isFinal: true }))]
  if (current) list.push(Object.assign([{ transcript: current.text }], { isFinal: current.isFinal }))
  return { resultIndex: current ? list.length - 1 : Math.max(0, list.length - 1), results: list }
}

/** The agent says one line and finishes its turn. */
async function agentSays(s: ReturnType<typeof setup>, text: string, turnId = 't1') {
  ingestAll(s.session, [
    { type: 'conversation', id: 'c', turnStarted: true, turnId, seq: 1 },
    { type: 'say', index: 0, text, turnId, seq: 2 },
    { type: 'done', conversationId: 'c', reason: 'end_turn', turnId, seq: 3 },
  ])
  s.tts.synth.finish()
  await vi.waitFor(() => expect(s.engine.phase).toBe('listening'))
}

afterEach(() => {
  vi.useRealTimers()
})

describe('WebKit recognition in a voice conversation', () => {
  test('deaf after playback with no end: the recogniser that heard the agent is replaced when it finishes', async () => {
    const s = setup()
    await s.engine.start()
    const first = s.rec.instances[0]!
    const mic: boolean[] = []
    s.engine.on('mic', (m) => mic.push(m))
    await agentSays(s, 'Your order ships tomorrow.')
    // iOS: the audio played; the running recogniser hears nothing more and never fires onend or onerror.
    first.say = () => undefined
    await vi.waitFor(() => expect(s.rec.instances).toHaveLength(2))
    const fresh = s.rec.instances[1]!
    expect(fresh.started).toBe(true)
    expect(first.started).toBe(false) // aborted
    // The microphone stayed open through the swap: no flicker of the mute icon.
    expect(mic).toEqual([])
    expect(s.engine.micOpen).toBe(true)
    fresh.say('and where is my refund', true)
    await vi.waitFor(() => expect(chats(s)).toEqual(['and where is my refund']))
  })

  test('the recogniser is not replaced while the user is still talking over the agent', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { settleMs: 500 } })
    await s.engine.start()
    ingestAll(s.session, [
      { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
      { type: 'say', index: 0, text: 'This week the shop refunded four thousand euros of orders.', turnId: 't1', seq: 2 },
    ])
    const r = s.rec.instances[0]!
    r.say('wait which order', false) // a barge-in: the agent stops, the user goes on
    s.session.ingest({ type: 'done', conversationId: 'c', reason: 'cancelled', turnId: 't1', seq: 3 })
    r.say('wait which order exactly', true)
    await vi.waitFor(() => expect(chats(s)).toEqual(['wait which order exactly']))
    expect(s.rec.instances).toHaveLength(1)
  })

  test('onend with an interim-only phrase: the phrase is sent before the restart', async () => {
    const s = setup({ engine: { settleMs: 0 } })
    await s.engine.start()
    const r = s.rec.instances[0]!
    r.say('show me the open orders', false)
    expect(chats(s)).toEqual([])
    r.onend?.()
    await vi.waitFor(() => expect(chats(s)).toEqual(['show me the open orders']))
    await vi.waitFor(() => expect(s.rec.instances).toHaveLength(2))
  })

  test('onend before the interim settled sends the phrase once', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { settleMs: 750 } })
    await s.engine.start()
    const r = s.rec.instances[0]!
    r.say('show me the open orders', false)
    vi.advanceTimersByTime(300)
    r.onend?.()
    vi.advanceTimersByTime(2000)
    await vi.waitFor(() => expect(chats(s)).toEqual(['show me the open orders']))
  })

  test('cumulative results: a later result that repeats the earlier phrases sends only the new words', async () => {
    const s = setup({ engine: { settleMs: 0 } })
    await s.engine.start()
    const r = s.rec.instances[0]!
    r.onresult?.(results([], { text: 'Hello there', isFinal: false }))
    r.onresult?.(results([], { text: 'Hello there.', isFinal: true }))
    await vi.waitFor(() => expect(chats(s)).toEqual(['Hello there.']))
    r.onresult?.(results(['Hello there.'], { text: 'Hello there. Show me', isFinal: false }))
    r.onresult?.(results(['Hello there.'], { text: 'Hello there. Show me the returns.', isFinal: true }))
    await vi.waitFor(() => expect(chats(s)).toEqual(['Hello there.', 'Show me the returns.']))
  })

  test('results never final: settled phrases are sent once, the cumulative one only for its new words', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { settleMs: 500 } })
    await s.engine.start()
    const r = s.rec.instances[0]!
    r.say('which order', false)
    vi.advanceTimersByTime(600)
    r.say('which order exactly', false)
    vi.advanceTimersByTime(600)
    await vi.waitFor(() => expect(chats(s)).toEqual(['which order', 'exactly']))
  })

  test('a new phrase from a recogniser that never made the last one final is sent in full', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { settleMs: 500 } })
    await s.engine.start()
    const r = s.rec.instances[0]!
    r.say('hello there', false)
    vi.advanceTimersByTime(600)
    r.say('show me returns', false)
    vi.advanceTimersByTime(600)
    await vi.waitFor(() => expect(chats(s)).toEqual(['hello there', 'show me returns']))
  })

  test('a short reply right after the agent spoke is sent, even with three of its words', async () => {
    const s = setup()
    await s.engine.start()
    await agentSays(s, 'Do you want the blue one or the red one?')
    await vi.waitFor(() => expect(s.rec.instances).toHaveLength(2))
    s.rec.instances[1]!.say('the red one', true)
    await vi.waitFor(() => expect(chats(s)).toEqual(['the red one']))
  })

  test('a recogniser error ends voice mode as before, even after the user was heard', async () => {
    const s = setup()
    const errors: string[] = []
    s.engine.on('error', (e) => errors.push(e.code))
    await s.engine.start()
    s.rec.instances[0]!.say('hello there', true)
    await vi.waitFor(() => expect(chats(s)).toHaveLength(1))
    s.rec.instances[0]!.onerror?.({ error: 'audio-capture' })
    await vi.waitFor(() => expect(s.engine.state).toBe('error'))
    expect(errors).toEqual(['audio-capture'])
  })

  test('a recogniser that ends as soon as it starts is restarted every time', async () => {
    const s = setup()
    await s.engine.start()
    for (let k = 0; k < 5; k++) {
      await vi.waitFor(() => expect(s.rec.instances).toHaveLength(k + 1))
      s.rec.instances[k]!.onend?.()
    }
    await vi.waitFor(() => expect(s.rec.instances).toHaveLength(6))
    expect(s.engine.micOpen).toBe(true)
    expect(s.engine.state).toBe('active')
  })

  test('muting while the recogniser is being replaced leaves the microphone closed', async () => {
    const s = setup()
    await s.engine.start()
    const mic: boolean[] = []
    s.engine.on('mic', (m) => mic.push(m))
    await agentSays(s, 'Your order ships tomorrow.')
    // The agent finished: the replacement is under way when the user mutes.
    s.engine.setMuted(true)
    await new Promise((r) => setTimeout(r, 5))
    expect(s.engine.micOpen).toBe(false)
    expect(mic).toEqual([false])
    expect(s.rec.instances.filter((r) => r.started)).toHaveLength(0)
  })
})

describe('starting and stopping', () => {
  test('an abort while the recogniser is still starting leaves it closed; of two starts only one recogniser runs', async () => {
    const rec = fakeRecognition('available')
    const stt = new OnDeviceStt({ recognition: rec.C })
    const starting = stt.start({ continuous: true })
    stt.abort()
    await starting
    expect(rec.instances).toHaveLength(0)
    expect(stt.running).toBe(false)
    await Promise.all([stt.start({ continuous: true }), stt.start({ continuous: true })])
    expect(rec.instances).toHaveLength(1)
  })
})

describe('repeated phrases', () => {
  test('the words already final are dropped from the start of a later result, and only there', () => {
    expect(withoutRepeat('Hello there. Show me the returns.', ' Hello there.')).toBe('Show me the returns.')
    expect(withoutRepeat('hello there', 'hello there')).toBe('hello there')
    expect(withoutRepeat('show me hello there', 'hello there')).toBe('show me hello there')
    expect(withoutRepeat('anything', '')).toBe('anything')
  })
})
