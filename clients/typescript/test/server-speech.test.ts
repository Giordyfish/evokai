import { speak, speechEvents, transcribe, transcribeSocketUrl, type SpeechSession } from '../src/browser/speech-server'
import { TimedSpeechPlayer, type PlaybackContext } from '../src/browser/tts/player'
import { ServerTts } from '../src/browser/tts/server'
import { ServerStt, toPcm16, type SocketLike } from '../src/browser/stt/server'
import { BrowserVoiceEngine } from '../src/browser/voice/browser'
import { EvokaiSession } from '../src/browser/session'

describe('the browser voice engine follows the token', () => {
  test('server engines when the token chose them, the device otherwise', () => {
    const chosen = new EvokaiSession({ baseUrl: 'http://evokai.test/agent', token: { token: 't', speech: { tts: 'elevenlabs', stt: 'openai' } } })
    expect(chosen.speech).toEqual({ tts: 'elevenlabs', stt: 'openai' })
    const server = new BrowserVoiceEngine({ session: chosen })
    expect(server.tts.kind).toBe('server')
    expect(server.stt.kind).toBe('server')
    const plain = new EvokaiSession({ baseUrl: 'http://evokai.test/agent', token: 't' })
    expect(new BrowserVoiceEngine({ session: plain }).stt.kind).toBe('on-device')
  })
})

const ev = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`
const b64 = (bytes: number[]) => btoa(String.fromCharCode(...bytes))

const STREAM = [
  ev('start', { engine: 'fake', voice: null, format: 'pcm', sampleRate: 1000, channels: 1, text: 'Hi there.', spoken: 'Hi there.' }),
  ev('audio', { data: b64([0, 16, 0, 16, 0, 16, 0, 16]), ms: 0 }),
  ev('word', { text: 'Hi', at: 0, length: 2, start: 0 }),
  ev('word', { text: 'there', at: 3, length: 5, start: 2 }),
  ev('mark', { name: 'wave', at: 3, time: 2 }),
  ev('viseme', { id: 4, time: 1 }),
  ev('done', { durationMs: 4, characters: 9, estimated: false }),
]

function session(body: string[] | (() => Response), calls: Array<{ url: string; init: RequestInit }> = []): SpeechSession {
  return {
    url: (p) => `http://evokai.test/agent${p}`,
    getAccessToken: async () => 'tok',
    fetchLike: (async (url: string, init: RequestInit) => {
      calls.push({ url, init })
      if (typeof body === 'function') return body()
      const enc = new TextEncoder()
      const stream = new ReadableStream<Uint8Array>({
        start(c) {
          for (const chunk of body) c.enqueue(enc.encode(chunk))
          c.close()
        },
      })
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }) as SpeechSession['fetchLike'],
  }
}

describe('server speech', () => {
  test('speak collects the audio and the timings', async () => {
    const calls: Array<{ url: string; init: RequestInit }> = []
    const r = await speak(session(STREAM, calls), { turnId: 't1', index: 0 })
    expect(r.start.text).toBe('Hi there.')
    expect([...r.audio]).toEqual([0, 16, 0, 16, 0, 16, 0, 16])
    expect(r.timings.words.map((w) => w.text)).toEqual(['Hi', 'there'])
    expect(r.timings.marks).toEqual([{ name: 'wave', at: 3, time: 2 }])
    expect(r.timings.visemes).toEqual([{ id: 4, time: 1 }])
    expect(r.done.durationMs).toBe(4)
    expect(calls[0]?.url).toBe('http://evokai.test/agent/v1/session/speech')
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ turnId: 't1', index: 0 })
    expect((calls[0]?.init.headers as Record<string, string>).Authorization).toBe('Bearer tok')
  })

  test('character times, the language and the speech followed travel as asked', async () => {
    const calls: Array<{ url: string; init: RequestInit }> = []
    const stream = [
      ev('start', { engine: 'fake', voice: null, format: 'pcm', sampleRate: 1000, channels: 1, text: 'Hi.', spoken: 'Hi.', language: 'it', speechId: 'sp_2' }),
      ev('chars', { at: 0, times: [[0, 1], [1, 2]] }),
      ev('chars', { at: 2, times: [[-1, -1]] }),
      ev('done', { durationMs: 2, characters: 3, estimated: false }),
    ]
    const r = await speak(session(stream, calls), { text: 'Hi.', language: 'it', after: ['sp_1'], characters: true })
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ text: 'Hi.', language: 'it', after: ['sp_1'], characters: true })
    expect(r.start.speechId).toBe('sp_2')
    expect(r.timings.chars).toEqual([[0, 1], [1, 2], [-1, -1]])
  })

  test('an engine error and a refused request are errors', async () => {
    await expect(speak(session([STREAM[0]!, ev('error', { code: 'tts_failed', message: 'no' })]), { text: 'x' })).rejects.toMatchObject({ code: 'tts_failed' })
    const refused = () => new Response(JSON.stringify({ error: 'limit', message: 'cap' }), { status: 429 })
    await expect(speechEvents(session(refused), { text: 'x' }).next()).rejects.toMatchObject({ status: 429, code: 'limit' })
  })

  test('transcribe posts the recording and the socket URL never carries the token', async () => {
    const calls: Array<{ url: string; init: RequestInit }> = []
    const s = session(() => new Response(JSON.stringify({ text: 'hello', durationMs: 500, engine: 'fake' })), calls)
    const r = await transcribe(s, new Uint8Array([1, 2]), { language: 'it-IT', contentType: 'audio/wav' })
    expect(r.text).toBe('hello')
    expect(calls[0]?.url).toBe('http://evokai.test/agent/v1/session/transcribe?language=it-IT')
    await transcribe(s, new Uint8Array([1, 2]), { hint: 'Evokai, Pipecat' })
    expect(calls[1]?.url).toBe('http://evokai.test/agent/v1/session/transcribe?hint=Evokai%2C+Pipecat')
    expect(transcribeSocketUrl(s)).toBe('ws://evokai.test/agent/v1/session/transcribe/stream')
  })
})

class FakeContext implements PlaybackContext {
  currentTime = 0
  destination = {}
  started: number[] = []
  createBuffer(_c: number, length: number) {
    const data = new Float32Array(length)
    return { getChannelData: () => data }
  }
  createBufferSource() {
    return { buffer: null as unknown, connect: () => undefined, start: (when?: number) => this.started.push(when ?? 0), stop: () => undefined }
  }
}

async function* events(list: string[]) {
  const s = session(list)
  yield* speechEvents(s, { text: 'x' })
}

describe('TimedSpeechPlayer', () => {
  afterEach(() => vi.useRealTimers())

  test('words, marks and visemes fire when the audio reaches them', async () => {
    vi.useFakeTimers()
    const ctx = new FakeContext()
    const p = new TimedSpeechPlayer({ context: ctx, leadMs: 0 })
    const got: string[] = []
    p.on('word', (w) => got.push(`word:${w.text}`))
    p.on('mark', (m) => got.push(`mark:${m.name}`))
    p.on('viseme', (v) => got.push(`viseme:${v.id}`))
    p.on('end', (e) => got.push(`end:${e.cancelled}`))
    const done = p.play(events(STREAM))
    await vi.advanceTimersByTimeAsync(0)
    expect(ctx.started).toEqual([0])
    expect(got).toEqual(['word:Hi'])
    await vi.advanceTimersByTimeAsync(1)
    expect(got).toEqual(['word:Hi', 'viseme:4'])
    await vi.advanceTimersByTimeAsync(1)
    expect(got).toEqual(['word:Hi', 'viseme:4', 'word:there', 'mark:wave'])
    await vi.advanceTimersByTimeAsync(5)
    expect(await done).toEqual({ cancelled: false })
    expect(got.at(-1)).toBe('end:false')
  })

  test('character times fire as each code point is heard, unknown ones never', async () => {
    vi.useFakeTimers()
    const p = new TimedSpeechPlayer({ context: new FakeContext(), leadMs: 0 })
    const got: string[] = []
    p.on('char', (c) => got.push(`${c.at}@${c.start}-${c.end}`))
    const stream = [STREAM[0]!, STREAM[1]!, ev('chars', { at: 0, times: [[0, 1], [-1, -1], [2, 3]] }), STREAM.at(-1)!]
    const done = p.play(events(stream))
    await vi.advanceTimersByTimeAsync(0)
    expect(got).toEqual(['0@0-1'])
    await vi.advanceTimersByTimeAsync(2)
    expect(got).toEqual(['0@0-1', '2@2-3'])
    await vi.advanceTimersByTimeAsync(5)
    expect(await done).toEqual({ cancelled: false })
  })

  test('stop cuts it off', async () => {
    vi.useFakeTimers()
    const p = new TimedSpeechPlayer({ context: new FakeContext(), leadMs: 0 })
    const done = p.play(events(STREAM))
    await vi.advanceTimersByTimeAsync(0)
    p.stop()
    expect(await done).toEqual({ cancelled: true })
  })
})

describe('ServerTts', () => {
  test('speaks a say segment by its turn and index, with boundaries and marks', async () => {
    const calls: Array<{ url: string; init: RequestInit }> = []
    const tts = new ServerTts({ session: session(STREAM, calls), context: new FakeContext(), leadMs: 0 })
    const seen: string[] = []
    tts.on('boundary', (b) => seen.push(`b${b.charIndex}`))
    tts.on('mark', (m) => seen.push(`m:${m.mark.name}`))
    tts.on('end', (e) => seen.push(`end:${e.cancelled}`))
    const u = tts.speak('Hi there.', { say: { turnId: 't1', index: 2 } })
    expect(await u.done).toEqual({ cancelled: false })
    expect(JSON.parse(String(calls[0]?.init.body))).toMatchObject({ turnId: 't1', index: 2, format: 'pcm' })
    expect(seen).toEqual(['b0', 'b3', 'm:wave', 'end:false'])
    expect(tts.kind).toBe('server')
  })
})

class FakeSocket implements SocketLike {
  binaryType = 'blob'
  readyState = 0
  sent: Array<string | ArrayBufferLike | ArrayBufferView> = []
  onopen: ((e: unknown) => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onclose: ((e: { code: number; reason?: string }) => void) | null = null
  onerror: ((e: unknown) => void) | null = null
  constructor(
    readonly url: string,
    readonly protocols: string[],
  ) {
    setTimeout(() => {
      this.readyState = 1
      this.onopen?.({})
    }, 0)
  }
  send(data: string | ArrayBufferLike | ArrayBufferView) {
    this.sent.push(data)
  }
  close() {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }
}

describe('ServerStt', () => {
  test('streams PCM over the socket with the token as a subprotocol and emits transcripts', async () => {
    let socket: FakeSocket | null = null
    let push: ((pcm: Int16Array) => void) | null = null
    const stt = new ServerStt({
      session: session([]),
      lang: 'it-IT',
      socket: (url, protocols) => (socket = new FakeSocket(url, protocols)),
      source: { start: async (on) => void (push = on), stop: () => undefined },
    })
    const finals: string[] = []
    stt.on('final', (t) => finals.push(t))
    await stt.start({ continuous: true })
    const s = socket as unknown as FakeSocket
    expect(s.url).toBe('ws://evokai.test/agent/v1/session/transcribe/stream')
    expect(s.protocols).toEqual(['evokai.v1', 'evokai.token.tok'])
    expect(JSON.parse(String(s.sent[0]))).toEqual({ type: 'start', sampleRate: 16000, language: 'it-IT' })
    push!(new Int16Array([1, 2, 3]))
    expect((s.sent[1] as ArrayBuffer).byteLength).toBe(6)
    s.onmessage?.({ data: JSON.stringify({ type: 'final', text: 'ciao' }) })
    expect(finals).toEqual(['ciao'])
    stt.stop()
    expect(JSON.parse(String(s.sent.at(-1)))).toEqual({ type: 'stop' })
  })

  test('client-decided utterances: no detection, a hint, commits, speech marks and warnings', async () => {
    let socket: FakeSocket | null = null
    const stt = new ServerStt({
      session: session([]),
      vad: false,
      hint: 'Evokai, Pipecat',
      socket: (url, protocols) => (socket = new FakeSocket(url, protocols)),
      source: { start: async () => undefined, stop: () => undefined },
    })
    const got: unknown[] = []
    stt.on('speechstart', (m) => got.push(['start', m]))
    stt.on('speechend', (m) => got.push(['end', m]))
    stt.on('warning', (w) => got.push(['warning', w.code]))
    stt.on('final', (t) => got.push(['final', t]))
    await stt.start({ continuous: true })
    const s = socket as unknown as FakeSocket
    expect(JSON.parse(String(s.sent[0]))).toEqual({ type: 'start', sampleRate: 16000, vad: false, hint: 'Evokai, Pipecat' })
    stt.commit()
    expect(JSON.parse(String(s.sent.at(-1)))).toEqual({ type: 'commit' })
    s.onmessage?.({ data: JSON.stringify({ type: 'speech_start', ms: 120 }) })
    s.onmessage?.({ data: JSON.stringify({ type: 'speech_end', ms: 900 }) })
    s.onmessage?.({ data: JSON.stringify({ type: 'final', text: 'ciao' }) })
    s.onmessage?.({ data: JSON.stringify({ type: 'warning', code: 'commit_too_short', message: 'ignored' }) })
    expect(got).toEqual([['start', { ms: 120 }], ['end', { ms: 900 }], ['final', 'ciao'], ['warning', 'commit_too_short']])
    expect(stt.running).toBe(true)
    stt.abort()
  })

  test('toPcm16 downsamples and clips', () => {
    const out = toPcm16(new Float32Array([0, 0, 1, 1, -1, -1, 2, 2]), 32000, 16000)
    expect([...out]).toEqual([0, 32767, -32768, 32767])
  })
})

describe('TimedSpeechPlayer under a slow engine', () => {
  afterEach(() => vi.useRealTimers())

  test('the clock starts with the first audio, and a late chunk shifts what follows', async () => {
    vi.useFakeTimers()
    const ctx = new FakeContext()
    const p = new TimedSpeechPlayer({ context: ctx, leadMs: 0 })
    const got: string[] = []
    p.on('word', (w) => got.push(`word:${w.text}@${ctx.currentTime}`))
    let release: (() => void) | null = null
    let release2: (() => void) | null = null
    const gate = new Promise<void>((r) => (release = r))
    const gate2 = new Promise<void>((r) => (release2 = r))
    async function* slow() {
      yield { type: 'start' as const, engine: 'x', voice: null, format: 'pcm' as const, sampleRate: 1000, channels: 1, text: 'a b', spoken: 'a b' }
      await gate
      yield { type: 'audio' as const, data: new Uint8Array([0, 16, 0, 16]), ms: 0 }
      yield { type: 'word' as const, text: 'a', at: 0, length: 1, start: 0 }
      await gate2
      yield { type: 'audio' as const, data: new Uint8Array([0, 16, 0, 16]), ms: 2 }
      yield { type: 'word' as const, text: 'b', at: 2, length: 1, start: 2 }
      yield { type: 'done' as const, durationMs: 4, characters: 3, estimated: false }
    }
    const done = p.play(slow())
    await vi.advanceTimersByTimeAsync(0)
    ctx.currentTime = 0.5 // the engine took 500 ms for its first byte
    release!()
    await vi.advanceTimersByTimeAsync(0)
    expect(ctx.started).toEqual([0.5])
    expect(got).toEqual(['word:a@0.5'])
    ctx.currentTime = 0.6 // the second chunk is 98 ms late (it was due at 0.502)
    release2!()
    await vi.advanceTimersByTimeAsync(0)
    expect(ctx.started).toEqual([0.5, 0.6])
    await vi.advanceTimersByTimeAsync(10)
    expect(got.map((g) => g.split('@')[0])).toEqual(['word:a', 'word:b'])
    await vi.advanceTimersByTimeAsync(10)
    expect(await done).toEqual({ cancelled: false })
  })
})

describe('ServerStt when the microphone fails', () => {
  test('the socket is closed and a retry can start again', async () => {
    const sockets: FakeSocket[] = []
    const errors: string[] = []
    const stt = new ServerStt({
      session: session([]),
      socket: (url, protocols) => {
        const s = new FakeSocket(url, protocols)
        sockets.push(s)
        return s
      },
      source: { start: async () => Promise.reject(new Error('not-allowed')), stop: () => undefined },
    })
    stt.on('error', (e) => errors.push(e.code))
    await expect(stt.start({ continuous: true })).rejects.toThrow('not-allowed')
    expect(sockets[0]?.readyState).toBe(3)
    expect(stt.running).toBe(false)
    expect(errors).toEqual(['not-allowed'])
  })
})
