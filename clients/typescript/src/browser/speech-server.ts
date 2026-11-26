/**
 * Server speech (`POST /v1/session/speech`): a text or one `say` segment of the conversation, spoken by the
 * session's speech engine, as audio and timings. The response is a stream of events: `start` (format, sample rate,
 * the text and its spoken form, the language, the `speechId`), `audio` (base64 PCM or Ogg Opus), `word` (offset
 * and length in the spoken text, start in ms), `chars` (character times, when asked for), `mark` (a cue of the
 * segment, with its time), `viseme`, `done` (duration).
 */
import { EvokaiError, type FetchLike } from '../core/http'
import { readSse } from './sse'

export interface SpeakInput {
  /** A text to speak (made speakable and redacted on the server). */
  text?: string
  /** Or a `say` segment of this conversation: its turn and index. */
  turnId?: string
  index?: number
  /** `pcm` (16-bit little-endian mono, the default) or `opus` (Ogg Opus, 48 kHz). */
  format?: 'pcm' | 'opus'
  sampleRate?: 16000 | 22050 | 24000 | 44100 | 48000
  /** The language spoken (BCP 47 or ISO 639-1); default the session's. */
  language?: string
  /**
   * For a text: the `speechId`s (from `start`) of earlier speech of this conversation it follows, oldest first, so
   * the voice carries on instead of starting anew. A `say` segment follows the earlier segments of its turn by itself.
   */
  after?: string[]
  /** Also stream character times (`chars` events), from engines that report them. */
  characters?: boolean
  signal?: AbortSignal
}

export interface SpeechStart {
  engine: string
  voice: string | null
  format: 'pcm' | 'opus'
  sampleRate: number
  channels: number
  /** What is shown (the subtitle). */
  text: string
  /** What the voice says (numbers in words). */
  spoken: string
  /** The language it is spoken in. */
  language?: string
  /** This speech, for `after` in a later request of the conversation. */
  speechId?: string
}
/** A word of the spoken text: `at` and `length` are characters of `spoken`, `start` milliseconds into the audio. */
export interface WordTiming {
  text: string
  at: number
  length: number
  start: number
}
/** A cue of the segment (`marks` of the `say` event) with the time it fires at. */
export interface MarkTiming {
  name: string
  arg?: string
  at: number
  time: number
}
/**
 * Character times: `times[i]` is `[startMs, endMs]` of code point `at + i` of `spoken`, or `[-1, -1]` for one the
 * engine did not report. Successive events continue where the last one ended and together cover `spoken`.
 */
export interface CharTimings {
  at: number
  times: Array<[number, number]>
}
export interface VisemeTiming {
  id: number
  time: number
}
export interface SpeechDone {
  durationMs: number
  characters: number
  /** The engine gave no word times: they are spread over the audio by the length of the text. */
  estimated: boolean
}

export type SpeechStreamEvent =
  | ({ type: 'start' } & SpeechStart)
  | { type: 'audio'; data: Uint8Array; ms: number | null }
  | ({ type: 'word' } & WordTiming)
  | ({ type: 'chars' } & CharTimings)
  | ({ type: 'mark' } & MarkTiming)
  | ({ type: 'viseme' } & VisemeTiming)
  | ({ type: 'done' } & SpeechDone)

export interface SpeechTimings {
  words: WordTiming[]
  marks: MarkTiming[]
  visemes: VisemeTiming[]
  /** `[startMs, endMs]` per code point of `start.spoken` (`[-1, -1]` where unknown); empty unless `characters` was asked and the engine reports them. */
  chars: Array<[number, number]>
}

export interface SpeechResult {
  start: SpeechStart
  /** The whole audio, in `start.format`. */
  audio: Uint8Array
  timings: SpeechTimings
  done: SpeechDone
}

/** What the speech helpers need from a session (an `EvokaiSession` is one). */
export interface SpeechSession {
  url(path: string): string
  getAccessToken(force?: boolean): Promise<string>
  readonly fetchLike: FetchLike
}

function fromBase64(s: string): Uint8Array {
  const bin = atob(s)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

async function errorOf(res: Response): Promise<EvokaiError> {
  let body: { error?: string; message?: string } = {}
  try {
    body = (await res.json()) as typeof body
  } catch {
    /* not JSON */
  }
  return new EvokaiError(res.status, body.error ?? 'http_error', body.message ?? res.statusText)
}

/** Speak and stream: each event as it arrives (audio first, timings as the engine reports them). */
export async function* speechEvents(session: SpeechSession, input: SpeakInput): AsyncGenerator<SpeechStreamEvent> {
  const { signal, ...body } = input
  const send = async (force: boolean) =>
    session.fetchLike(session.url('/v1/session/speech'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', Authorization: `Bearer ${await session.getAccessToken(force)}` },
      body: JSON.stringify(body),
      ...(signal ? { signal } : {}),
    })
  let res = await send(false)
  if (res.status === 401) res = await send(true)
  if (!res.ok || !res.body) throw await errorOf(res)
  for await (const msg of readSse(res.body, signal)) {
    const data = JSON.parse(msg.data) as Record<string, unknown>
    if (msg.event === 'error') throw new EvokaiError(502, String(data.code ?? 'tts_failed'), String(data.message ?? 'Speech failed'))
    if (msg.event === 'audio') yield { type: 'audio', data: fromBase64(String(data.data)), ms: (data.ms as number | null) ?? null }
    else if (
      msg.event === 'start' ||
      msg.event === 'word' ||
      msg.event === 'chars' ||
      msg.event === 'mark' ||
      msg.event === 'viseme' ||
      msg.event === 'done'
    ) {
      yield { type: msg.event, ...data } as SpeechStreamEvent
    }
  }
}

/** Speak and wait: the whole audio and every timing. */
export async function speak(session: SpeechSession, input: SpeakInput): Promise<SpeechResult> {
  let start: SpeechStart | null = null
  let done: SpeechDone | null = null
  const chunks: Uint8Array[] = []
  const timings: SpeechTimings = { words: [], marks: [], visemes: [], chars: [] }
  for await (const e of speechEvents(session, input)) {
    if (e.type === 'start') start = strip(e)
    else if (e.type === 'audio') chunks.push(e.data)
    else if (e.type === 'word') timings.words.push(strip(e))
    else if (e.type === 'chars') e.times.forEach((t, i) => (timings.chars[e.at + i] = t))
    else if (e.type === 'mark') timings.marks.push(strip(e))
    else if (e.type === 'viseme') timings.visemes.push(strip(e))
    else if (e.type === 'done') done = strip(e)
  }
  if (!start || !done) throw new EvokaiError(502, 'tts_failed', 'The speech stream ended early')
  const audio = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0))
  let at = 0
  for (const c of chunks) {
    audio.set(c, at)
    at += c.length
  }
  return { start, audio, timings, done }
}

function strip<T extends { type: string }>(e: T): Omit<T, 'type'> {
  const { type: _type, ...rest } = e
  return rest
}

/** The URL of the streaming recognition socket (`ws:` or `wss:`), for `ServerStt`. */
export function transcribeSocketUrl(session: Pick<SpeechSession, 'url'>): string {
  return session.url('/v1/session/transcribe/stream').replace(/^http/, 'ws')
}

/** Transcribe a recording (a `Blob` or bytes: WAV, WebM, Ogg, MP3, M4A) with the session's recognition engine. */
export async function transcribe(
  session: SpeechSession,
  audio: Blob | Uint8Array,
  opts: { language?: string; contentType?: string; signal?: AbortSignal } = {},
): Promise<{ text: string; durationMs: number; engine: string }> {
  const type = opts.contentType ?? (audio instanceof Blob && audio.type ? audio.type : 'audio/wav')
  const url = session.url('/v1/session/transcribe') + (opts.language ? `?language=${encodeURIComponent(opts.language)}` : '')
  const send = async (force: boolean) =>
    session.fetchLike(url, {
      method: 'POST',
      headers: { 'Content-Type': type, Authorization: `Bearer ${await session.getAccessToken(force)}` },
      body: audio as BodyInit,
      ...(opts.signal ? { signal: opts.signal } : {}),
    })
  let res = await send(false)
  if (res.status === 401) res = await send(true)
  if (!res.ok) throw await errorOf(res)
  return (await res.json()) as { text: string; durationMs: number; engine: string }
}
