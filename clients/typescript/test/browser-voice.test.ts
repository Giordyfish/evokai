import { EvokaiSession } from '../src/browser/session'
import { BrowserVoiceEngine } from '../src/browser/voice/browser'
import { Dictation } from '../src/browser/voice/dictation'
import { recognitionSupport } from '../src/browser/stt/on-device'
import { BrowserTts, loadedVoices, pickLocalVoice, pickVoice } from '../src/browser/tts/browser'
import { Microphone } from '../src/browser/mic/microphone'
import { mockFetch } from './helpers/fetch'
import { EN_CLOUD, EN_LOCAL, fakeRecognition, fakeSynthesis, ingestAll, setup } from './helpers/voice'
// ── tests ────────────────────────────────────────────────────────────────────────────────────────────────────

describe('on-device recognition only', () => {
  test('no on-device API means unsupported, even with a (cloud) recogniser present', async () => {
    const { engine, rec } = setup({ availability: null })
    const errors: string[] = []
    engine.on('error', (e) => errors.push(e.code))
    await engine.start()
    expect(engine.state).toBe('unsupported')
    expect(errors).toEqual(['unsupported'])
    expect(rec.instances).toHaveLength(0)
  })

  test('an unavailable language pack is unsupported; a downloadable one is installed first', async () => {
    expect(await recognitionSupport({ recognition: fakeRecognition('unavailable').C })).toBe('unsupported')
    const { engine, rec } = setup({ availability: 'downloadable' })
    await engine.start()
    // The last check: whether recognition runs on the device (it does), before the recogniser starts.
    expect(rec.calls.map((c) => c.fn)).toEqual(['available', 'available', 'install', 'available', 'available'])
    expect(rec.calls.every((c) => c.processLocally && c.langs[0] === 'en-US')).toBe(true)
    expect(engine.state).toBe('active')
  })

  test('the recogniser runs locally, continuously, with interim results', async () => {
    const { engine, rec } = setup()
    await engine.start()
    const r = rec.instances[0]!
    expect([r.processLocally, r.continuous, r.interimResults, r.lang, r.started]).toEqual([true, true, true, 'en-US', true])
  })

  test('dictation uses the same on-device recogniser, one-shot, and sends nothing to the agent', async () => {
    const rec = fakeRecognition('available')
    const d = new Dictation({ recognition: rec.C, microphone: new Microphone({ mediaDevices: null }), meterMicrophone: false, micSound: 'none' })
    const texts: string[] = []
    const finals: string[] = []
    d.on('text', (t) => texts.push(t))
    d.on('final', (t) => finals.push(t))
    await d.start()
    expect(d.state).toBe('listening')
    const r = rec.instances[0]!
    expect([r.processLocally, r.continuous]).toEqual([true, false])
    r.say('hello everyone', false)
    r.say('hello everyone', true)
    expect(texts).toEqual(['hello everyone', 'hello everyone'])
    expect(finals).toEqual(['hello everyone'])
    d.stop()
    await vi.waitFor(() => expect(d.state).toBe('idle'))
    const none = new Dictation({ recognition: fakeRecognition(null).C, microphone: new Microphone({ mediaDevices: null }), meterMicrophone: false })
    await none.start()
    expect(none.state).toBe('unsupported')
  })
})

describe('speaking', () => {
  test('only local voices are used', () => {
    expect(pickLocalVoice([EN_CLOUD, { name: 'x', lang: 'it-IT', localService: true }])).toBeNull()
    expect(pickLocalVoice([EN_CLOUD, EN_LOCAL])).toBe(EN_LOCAL)
  })

  test('online voices only when the application allows them', () => {
    const IT_CLOUD = { name: 'Google italiano', lang: 'it-IT', localService: false }
    expect(pickVoice([IT_CLOUD], 'it-IT')).toBeNull()
    expect(pickVoice([IT_CLOUD], 'it-IT', true)).toBe(IT_CLOUD)
    const IT_LOCAL = { name: 'Alice', lang: 'it-IT', localService: true }
    expect(pickVoice([IT_CLOUD, IT_LOCAL], 'it-IT', true)).toBe(IT_LOCAL)
  })

  test('without a local voice nothing is spoken unless online voices are allowed', async () => {
    const IT_CLOUD = { name: 'Google italiano', lang: 'it-IT', localService: false }
    const strict = fakeSynthesis([IT_CLOUD])
    const Utterance = class { text: string; lang = ''; rate = 1; voice = null; onstart = null; onend = null; onerror = null; constructor(t: string) { this.text = t } }
    const off = new BrowserTts({ locale: 'it', synthesis: strict.synth, Utterance, estimate: false })
    await off.prepare()
    expect(off.status.state).toBe('unavailable')
    expect((await off.speak('Ciao').done).cancelled).toBe(true)
    expect(strict.spoken).toEqual([])
    const open = fakeSynthesis([IT_CLOUD])
    const on = new BrowserTts({ locale: 'it', synthesis: open.synth, Utterance, estimate: false, allowOnlineVoices: true })
    await on.prepare()
    expect(on.status.state).toBe('ready')
    on.speak('Ciao')
    expect(open.spoken).toEqual(['Ciao'])
  })

  test('voices that load late are waited for before speaking', async () => {
    let voices: Array<{ name: string; lang: string; localService: boolean }> = []
    const listeners: Array<() => void> = []
    const spoken: string[] = []
    const synth = {
      getVoices: () => voices,
      speak: (u: { text: string }) => void spoken.push(u.text),
      cancel: () => undefined,
      addEventListener: (_: 'voiceschanged', fn: () => void) => void listeners.push(fn),
      removeEventListener: () => undefined,
    }
    const Utterance = class { text: string; lang = ''; rate = 1; voice = null; onstart = null; onend = null; onerror = null; constructor(t: string) { this.text = t } }
    const tts = new BrowserTts({ locale: 'it', synthesis: synth, Utterance, estimate: false })
    tts.speak('Buongiorno')
    expect(spoken).toEqual([])
    voices = [{ name: 'Alice', lang: 'it-IT', localService: true }]
    listeners.forEach((f) => f())
    await new Promise((r) => setTimeout(r, 0))
    expect(spoken).toEqual(['Buongiorno'])
    expect(await loadedVoices(synth)).toHaveLength(1)
  })

  test('say events are spoken in index order whatever order they arrive in', async () => {
    const { engine, session, tts } = setup()
    await engine.start()
    ingestAll(session, [
      { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
      { type: 'say', index: 1, text: 'Second sentence.', turnId: 't1', seq: 2 },
      { type: 'say', index: 0, text: 'First sentence.', turnId: 't1', seq: 3 },
      { type: 'say', index: 2, text: 'Third sentence.', turnId: 't1', seq: 4 },
    ])
    expect(engine.phase).toBe('speaking')
    tts.synth.finish()
    tts.synth.finish()
    expect(tts.spoken).toEqual(['First sentence.', 'Second sentence.', 'Third sentence.'])
  })

  test('no local voice: nothing is spoken and the TTS reports it unavailable', async () => {
    const { engine, session, tts } = setup({ voices: [EN_CLOUD] })
    const errors: string[] = []
    engine.tts.on('status', (st) => st.error && errors.push(st.error.code))
    await engine.start()
    ingestAll(session, [
      { type: 'say', index: 0, text: 'One.', turnId: 't1', seq: 1 },
      { type: 'say', index: 1, text: 'Two.', turnId: 't1', seq: 2 },
    ])
    expect(tts.spoken).toEqual([])
    expect(errors[0]).toBe('no_local_voice')
    expect(engine.tts.status.state).toBe('unavailable')
  })
})

describe('barge-in and spoken replies', () => {
  test('speaking over the agent stops synthesis and cancels the turn', async () => {
    const { engine, session, tts, rec, m } = setup()
    await engine.start()
    ingestAll(session, [
      { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
      { type: 'say', index: 0, text: 'This week the shop refunded four thousand euros of orders.', turnId: 't1', seq: 2 },
    ])
    expect(engine.phase).toBe('speaking')
    const barge: unknown[] = []
    engine.on('bargeIn', (b) => barge.push(b))
    rec.instances[0]!.say('wait what about returns', false)
    expect(tts.synth.cancelled).toBeGreaterThanOrEqual(1)
    expect(engine.phase).toBe('interrupted')
    expect(barge).toEqual([{ turnId: 't1', heard: 'wait what about returns' }])
    await vi.waitFor(() => expect(m.calls.some((c) => c.url.endsWith('/turns/t1/cancel'))).toBe(true))
    // The rest of that turn is not spoken.
    session.ingest({ type: 'say', index: 1, text: 'More.', turnId: 't1', seq: 3 })
    expect(tts.spoken).toEqual(['This week the shop refunded four thousand euros of orders.'])
  })

  test('the agent’s own voice picked up by the microphone is ignored (echo guard)', async () => {
    const { engine, session, rec, m } = setup()
    await engine.start()
    ingestAll(session, [
      { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
      { type: 'say', index: 0, text: 'This week four thousand euros refunded, less than last week.', turnId: 't1', seq: 2 },
    ])
    rec.instances[0]!.say('four thousand euros refunded', false)
    rec.instances[0]!.say('four thousand euros refunded', true)
    expect(m.calls).toHaveLength(0)
    expect(engine.phase).toBe('speaking')
  })

  test('a spoken yes is sent as text in voice mode (the server matches it)', async () => {
    const { engine, session, rec, m } = setup()
    await engine.start()
    session.ingest({ type: 'confirm', actionId: 'a1', items: ['Refund order 1042'], turnId: 't0', seq: 1 })
    session.ingest({ type: 'done', conversationId: 'c', reason: 'confirm', turnId: 't0', seq: 2 })
    rec.instances[0]!.say('yes, go ahead', true)
    await vi.waitFor(() => expect(m.calls).toHaveLength(1))
    expect(m.calls[0]!.body).toMatchObject({ text: 'yes, go ahead', mode: 'voice' })
    expect(typeof (m.calls[0]!.body as { endOfSpeechAt: number }).endOfSpeechAt).toBe('number')
  })

  test('the recogniser restarts after it ends, until stopped', async () => {
    const { engine, rec } = setup()
    await engine.start()
    rec.instances[0]!.onend?.()
    await vi.waitFor(() => expect(rec.instances).toHaveLength(2))
    await engine.stop()
    expect(engine.state).toBe('idle')
    rec.instances[1]!.onend?.()
    await new Promise((r) => setTimeout(r, 5))
    expect(rec.instances).toHaveLength(2)
  })

  test('a refused microphone stops the engine with an error', async () => {
    const { engine, rec } = setup()
    const errors: string[] = []
    engine.on('error', (e) => errors.push(e.code))
    await engine.start()
    rec.instances[0]!.onerror?.({ error: 'not-allowed' })
    await vi.waitFor(() => expect(engine.state).toBe('error'))
    expect(errors).toEqual(['not-allowed'])
    expect(engine.phase).toBe('idle')
  })

  test('phase transitions: idle → listening → thinking → speaking → listening → idle', async () => {
    const { engine, session, tts, phases } = setup()
    await engine.start()
    session.ingest({ type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 })
    session.ingest({ type: 'say', index: 0, text: 'Done.', turnId: 't1', seq: 2 })
    session.ingest({ type: 'done', conversationId: 'c', reason: 'end_turn', turnId: 't1', seq: 3 })
    tts.synth.finish()
    await vi.waitFor(() => expect(engine.phase).toBe('listening'))
    await engine.stop()
    expect(phases).toEqual(['listening', 'thinking', 'speaking', 'listening', 'idle'])
  })

  test('an Italian session listens and speaks in Italian', async () => {
    const rec = fakeRecognition('available')
    const tts = fakeSynthesis([EN_LOCAL, { name: 'Alice', lang: 'it-IT', localService: true }])
    const session = new EvokaiSession({ baseUrl: 'https://a.test/agent', token: 'tok', fetch: mockFetch({}).fetch, locale: 'it' })
    const engine = new BrowserVoiceEngine({
      session,
      recognition: rec.C,
      ttsOptions: { device: 'phone', browser: { synthesis: tts.synth, Utterance: tts.Utterance } },
      microphone: new Microphone({ mediaDevices: null }),
      meterMicrophone: false,
      micSound: 'none',
    })
    await engine.start()
    expect(rec.instances[0]!.lang).toBe('it-IT')
    session.ingest({ type: 'say', index: 0, text: 'Ciao.', turnId: 't', seq: 1 })
    expect(tts.spoken).toEqual(['Ciao.'])
    await engine.stop()
  })
})
