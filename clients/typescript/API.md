# @evokai/evokai-client API

The client is **headless**. It gives an application everything it needs to build its own interface for an Evokai
agent, and it draws nothing.

- `/server`: backend SDK: session tokens, conversations, usage (amounts allowed: server to server), admin.
- `/browser`: the core: session, typed events, turn phases, microphone, voice (dictation, conversation, barge-in),
  speech engines chosen by device, audio signals for any visual, optional microphone sounds, usage as percentages.
- `/react`: thin hooks over `/browser`.

Everything listed here is implemented. Defaults are English; Italian is opt-in data (`locale: 'it'`, see §3,
"Locales"). Licences of the optional voice runtime are in §6.

---

## 1. Events

`session.on(type, fn)` delivers the service's events plus the client's `phase` event; `'*'` delivers all of them.
Every service event carries `turnId: string | null` and `seq: number`, a per-session sequence used for replay. Each
event reaches listeners exactly once: an event whose `seq` was already applied is dropped.

| `type` | Payload | Meaning |
| --- | --- | --- |
| `conversation` | `{ id, turnStarted?: true }` | A turn started in conversation `id`. |
| `text` | `{ delta }` | A piece of the answer (Markdown). |
| `say` | `{ index, text, filler?: true }` | Voice mode: a speakable segment, numbered from 0 in the turn. Voice engines speak these. |
| `tool` | `{ name, status: 'start' \| 'done' \| 'error' }` | Tool activity. |
| `chart` | `{ chart: Chart }` | A chart to show. |
| `diagram` | `{ diagram: Diagram }` | A schema to show. |
| `ask` | `{ askId, question, options: string[], multiple, other }` | A question with options. The turn ends with `reason: 'ask'`; answer with `session.answer`. |
| `navigate` | `{ path }` | The agent asks the application to open a page. |
| `confirm` | `{ actionId, items: string[], why?: ConfirmReason[] }` | A call waiting for the user (a change, or a call an ask rule asks for). `why[i]` says why `items[i]` waits: `{ key: 'confirm.why.writes' \| 'confirm.why.rule' \| 'confirm.why.policy', rule?, level?, label? }`; `confirmReasonText(why, pack)` gives the rule's label or the pack's sentence. The turn ends with `reason: 'confirm'`; answer with `session.confirm`. |
| `rewritten` | `{ replaced: string[], messageId?, replacedFrom? }` | The last question was rewritten: the first event of the new turn after `conversation`. Drop what you show of the `replaced` turns (the store does). A replay never returns their events. |
| `connect` | `{ provider: 'microsoft365' \| 'google', title, server }` | A tool needs the person to connect a provider: show a card whose button calls `session.startConnection(provider)` and opens `authorizeUrl`. |
| `usage` | `UsageShares` (`{ dayPct, weekPct, monthPct, companyPct, monthOut, out, binding, resetsAt, caps }`) | Shares of the caps after a model call. Never amounts. |
| `error` | `LimitErrorEvent \| OtherErrorEvent` | `{ code: 'limit', limit: LimitKind, message, resetsAt }` when a cap blocked the turn (the message says when it resets); otherwise `{ code, message }` (`internal`, `model`, `empty`, `expired`, …). `message` is safe to show. |
| `done` | `{ conversationId, reason, rewritable?, messages?, replacedFrom? }` | Always the last event of a turn. `rewritable`: whether the last question can still be rewritten. `replacedFrom`: external storage, a rewrite, drop your history from that index before appending `messages`. `reason`: `end_turn`, `confirm`, `ask`, `limit`, `max_steps`, `cancelled`, `empty`, `expired`, `model`, `internal`, `disconnected`, `barge_in`, `superseded`, `closed`. `messages`: with external storage, the new `HistoryMessage[]` to append. |
| `speech` | `{ enabled, reason?, message? }` | Server voice: speech went off (provider failure); answers continue in text. |
| `confirmed` | `{ approve, writes? }` | Only in stored history, on the user message that answered a confirmation (`writes`: a tool that changes data ran). |
| `phase` *(client)* | `{ phase: TurnPhase, previous: TurnPhase, turnId }` | The turn phase changed. |

`LimitKind = 'app_month' | 'app_week' | 'app_day' | 'subject_month' | 'subject_week' | 'subject_day'` (an eval run's
own budget reports `limit: 'eval'`). `isLimitError(e)` narrows an event to
`LimitErrorEvent`.

**Triggers.** An application's own interface typically reacts to these:

```ts
session.on('confirm', (e) => showConfirm(e.actionId, e.items))
session.on('ask', (e) => showOptions(e.askId, e.question, e.options, e.multiple, e.other))
session.on('navigate', (e) => router.push(e.path))
session.on('chart', (e) => drawChart(e.chart))
session.on('diagram', (e) => drawDiagram(e.diagram))
session.on('tool', (e) => showActivity(e.name, e.status))
session.on('error', (e) => (isLimitError(e) ? showLimit(e.limit, e.message) : showError(e.message)))
session.on('usage', (u) => showShare(u.dayPct, u.monthOut))
session.on('done', (e) => e.messages && appendToMyHistory(e.messages))
```

### Visual payloads

```ts
type Unit = 'eur' | 'count' | 'pct'
interface Point { label: string; value: number }
type Chart =
  | { type: 'bar' | 'column' | 'line' | 'area'; title; subtitle; unit: Unit; points: Point[]; highlight: string | null; name: string; compare: { name: string; points: Point[] } | null }
  | { type: 'donut' | 'stacked'; title; subtitle; unit: Unit; points: Point[] }  // ≤ 6 slices, the tail folded into one (label from the server)
  | { type: 'kpi'; title; subtitle; items: Array<{ label; value: number; unit: Unit; delta: number | null; note: string }> }
  | { type: 'image'; title; subtitle; mediaType: 'image/png' | 'image/jpeg'; data: string }  // drawn by the agent's code (code_run), base64, ≤ 512 kB
type Diagram =
  | { kind: 'steps'; title; steps: Array<{ label; note; state: 'done' | 'current' | 'todo' }> }
  | { kind: 'tree'; title; root: { label; note }; children: Array<{ label; note }> }
  | { kind: 'compare'; title; columns: [string, string]; rows: Array<{ label; left; right; differs: boolean }> }
```

### Usage

```ts
interface UsageShares {
  dayPct: number | null     // the subject's day, 0–100 (null: no daily cap)
  weekPct?: number | null   // the subject's week
  monthPct: number | null   // the subject's month
  companyPct: number | null // the application's month
  monthOut: boolean         // a monthly cap is reached
  out?: boolean             // any cap is reached: nothing more until resetsAt
  binding?: CapShare | null // the cap that limits most
  resetsAt?: string | null  // its next reset (ISO time, 00:00 in the application's time zone)
  caps?: CapShare[]         // every cap: { limit, scope, period, usedPct, leftPct, resetsAt }
}
```

Usage arrives in the session-token response (`usage`), in `GET /v1/session` and in every `usage` event. Read it from
`session.usage`, `session.subscribeUsage(fn)` (every change), `session.on('usage', fn)` or `useUsage()`. No colour
logic is applied: thresholds are up to the application.

---

### Handoff

`handoff` (`{ from, to, title, reason }`): the agent handed the conversation to another agent of the application
(listed in its `handoff_to`); from the next turn `to` answers, with the conversation so far. Show who takes over.

## 2. Turn phases

```ts
type TurnPhase = 'idle' | 'listening' | 'thinking' | 'speaking' | 'interrupted'
```

There is one phase per session: `session.phase`, `state.phase`, the `phase` event, `usePhase()`. Voice engines feed
it through `session.setVoiceActivity('listening' | 'speaking' | null)` and `session.markInterrupted()`. It is
derived as follows (`phaseOf`), first match wins:

1. `speaking`: the agent's voice is playing.
2. `interrupted`: the last turn was cut off (barge-in, stop, `cancelled`/`superseded`/`disconnected`/`closed`). It
   holds until the next activity.
3. `thinking`: a turn is running.
4. `listening`: a voice engine is waiting for the user.
5. `idle`: nothing is happening.

Typical transitions:

```
text only:      idle → thinking → idle
                idle → thinking → interrupted → thinking (next message)
voice:          idle → listening → thinking → speaking → listening → … → idle (stop)
barge-in:       speaking → interrupted → thinking (the new phrase) → speaking …
stop button:    thinking|speaking → interrupted → listening|idle (next activity)
```

---

## 3. `/browser` exports

### Session

```ts
class EvokaiSession {
  constructor(opts: EvokaiSessionOptions)
  readonly store: SessionStore
  readonly state: SessionState
  readonly phase: TurnPhase
  readonly usage: UsageShares | null
  readonly voiceEngine: 'browser' | 'server'          // as the token chose
  subscribe(fn: (s: SessionState) => void): () => void
  subscribeUsage(fn: (u: UsageShares) => void): () => void
  on<T extends SessionEventType | '*'>(type: T, fn: (e) => void): () => void
  ingest(event: EvokaiEvent): void                     // events from another channel (server voice)

  info(): Promise<SessionInfo>                         // { conversationId, agent, expiresAt, usage, memories, memory }
  loadConversation(): Promise<SessionConversation>     // resumes a running turn
  events(after?: number): Promise<EvokaiEvent[]>       // replay
  chat(input: ChatInput): Promise<DoneEvent | null>    // streams; null when aborted/failed (see state.error)
  confirm(actionId: string, approve: boolean, opts?): Promise<DoneEvent | null>
  answer(askId: string, labels: string[], opts?): Promise<DoneEvent | null>
  rewrite(text: string, opts?): Promise<DoneEvent | null>  // replace the last question; 409 rewrite_* in state.error
  cancel(turnId?: string | null): Promise<boolean>     // POST /v1/session/turns/{id}/cancel
  abort(): void                                        // close open streams
  resume(turnId?: string | null): Promise<DoneEvent | null>
  feedback(messageId: string, value: 1 | -1 | null): Promise<void>
  memories(): Promise<MemoryList>                      // { memory: MemoryState, items: Memory[] }
  memoryConsent(granted: boolean): Promise<{ memory: MemoryState; deleted: number }>   // 409 memory_off
  editMemory(id: string, text: string): Promise<Memory> // 400 memory_refused, 403 memory_inactive
  forgetMemory(id?: string): Promise<number>           // one memory, or everything (no id); how many were deleted
  exit(code: string): Promise<void>                    // kiosk sessions
  reportBug(description: string, context?: object): Promise<string>
  connections(): Promise<Connection[]>                 // one per granted provider: status, account, families
  startConnection(provider, returnTo?): Promise<ConnectionStarted>  // open authorizeUrl in a new window
  disconnect(provider): Promise<void>                  // the person's tokens are deleted
  switchConversation(conversationId: string | null): Promise<void>
  newConversation(): Promise<void>
  history(): Promise<HistoryMessage[] | null>          // external storage, from getHistory
  setVoiceActivity(a: 'listening' | 'speaking' | null): void
  markInterrupted(): void
  getAccessToken(force?: boolean): Promise<string>
  url(path: string): string
  dispose(): void
}

interface EvokaiSessionOptions {
  baseUrl: string                                   // e.g. https://agents.example.com/agent
  token?: TokenInfo
  getToken?: (ctx: { conversationId: string | null; reason: 'initial' | 'refresh' | 'switch' }) => TokenInfo | Promise<TokenInfo>
  fetch?: FetchLike
  getPage?: () => PageContext | null | undefined    // { path, title, text } sent with each message
  getHistory?: () => HistoryMessage[] | null | undefined | Promise<…>   // external storage
  onMessages?: (messages: HistoryMessage[], done: DoneEvent) => void    // external storage
  replay?: { attempts?: number; delayMs?: number }  // default 8 polls, 500 ms growing
  voiceEngine?: 'browser' | 'server'                // when the token is a bare string
}
type TokenInfo = string | { token; conversationId?; voiceEngine?; expiresAt?; usage? }   // the mint response fits

interface ChatInput {
  text?: string; confirm?: { actionId; approve }; page?: PageContext | null; mode?: 'text' | 'voice'
  history?: HistoryMessage[] | null; endOfSpeechAt?: number; answer?: boolean; rewrite?: boolean; signal?: AbortSignal
}

function isLimitError(e: SessionEvent): e is LimitErrorEvent
```

Long-term memory (see the [README](README.md#long-term-memory)):

```ts
type MemoryKind = 'preference' | 'semantic' | 'episodic'   // how they like answers, their work, past conversations
interface MemoryState {
  mode: 'off' | 'ask' | 'on'                    // the application's memory_mode
  consent: 'granted' | 'declined' | 'unknown'   // the person's answer
  active: boolean                               // memory is on for the person now
  ask: boolean                                  // ask them once (mode ask, no answer yet)
}
interface Memory {
  id: string; text: string; kind: MemoryKind; agent: string | null
  source: 'agent' | 'summary' | 'person'        // person: rewritten by the person
  createdAt: string | null; updatedAt: string | null; expiresAt: string | null; lastUsedAt: string | null; uses: number
}
interface MemoryList { memory: MemoryState; items: Memory[] }
```

On the server (`/server`): `mintSessionToken({ ..., memoryConsent?: boolean })` records the answer the person gave in
your interface and the response carries `memory`; `memories.list(subject)`, `memories.consent(subject, granted)` (`{
memory, deleted }`) and `memories.forget(subject, id?)`.

HTTP failures are `EvokaiError`s with `status` and `code` (`KnownErrorCode`). A session whose conversation passed the
application's `conversation_ttl_days` fails with 404 `expired_conversation` (`isExpiredConversation(e)`): ask
`getToken` for a new conversation.

`getToken` is called on first use, on a 401 (once, then the request is retried), within 30 s of `expiresAt`, and on a
conversation switch.

### State (`SessionState`, from the pure reducer `reduce`)

```ts
interface SessionState {
  conversationId: string | null
  messages: ChatMessage[]            // { id, role, parts: Part[], turnId?, messageId?, feedback?, answer?, interrupted? }
  currentTurn: { turnId: string | null } | null
  busy: boolean
  phase: TurnPhase
  pendingConfirm: { actionId; items } | null
  openAsk: { askId; question; options; multiple; other } | null
  usage: UsageShares | null
  voiceEngine: 'browser' | 'server' | null
  voiceActivity: 'listening' | 'speaking' | null
  interrupted: boolean
  speech: { enabled: boolean; message?: string }
  lastSeq: number; lastDone: DoneEvent | null
  error: { code: string; message: string } | null
}
type Part =
  | { kind: 'text'; text } | { kind: 'tool'; name; status } | { kind: 'chart'; chart } | { kind: 'diagram'; diagram }
  | { kind: 'ask'; askId; question; options; multiple; other; answered: string[] | null }
  | { kind: 'navigate'; path } | { kind: 'confirm'; actionId; items; why?; state: 'open' | 'approved' | 'rejected' | 'closed' }
  | { kind: 'error'; code; message }
```

Also exported: `SessionStore`, `reduce`, `initialState`, `fromStored`, `fromHistory`, `phaseOf`, `usageOf`, `seen`,
`SseParser`, `readSse`, and the speech text rules shared with the server: `speakable`, `SentenceSplitter`,
`spokenDecision`, `spokenOption`, `isEcho`, `norm`, `redact`; and the voice helpers `meaningfulWords(text, locale)`,
`stripEcho(heard, spoken)`, `tailWords(text, n = 4)`, `FILLER_WORDS`.

### AG-UI

`runAgui({ baseUrl, token, input, fetch?, signal? })` posts an AG-UI `RunAgentInput` to `/v1/session/agui` and yields
the run's events typed as `AguiEvent` (`RUN_STARTED`, `TEXT_MESSAGE_*`, `TOOL_CALL_*`, `STATE_SNAPSHOT`,
`STATE_DELTA`, `CUSTOM`, `RUN_FINISHED`, `RUN_ERROR`); aborting the signal cancels the turn. `interruptsOf(event)`
lists what a finished run waits on (`confirmation` or `input_required`), to answer with `input.resume`. See
[docs/ag-ui.md](../../docs/ag-ui.md).

### Microphone

```ts
type MicPermission = 'granted' | 'denied' | 'prompt' | 'unsupported'
interface MicDevice { deviceId; label; groupId; isDefault }
interface MicState { permission; active; muted; deviceId: string | null; devices: MicDevice[] }

class Microphone {
  constructor(opts?: { constraints?; deviceId?; mediaDevices?; permissions?; audioContext? })
  readonly state: MicState
  readonly stream: MediaStream | null
  readonly track: MediaStreamTrack | null
  permission(): Promise<MicPermission>          // no prompt
  requestPermission(): Promise<MicPermission>   // prompts (opens and closes once)
  start(deviceId?: string | null): Promise<MediaStream>
  stop(): void
  setMuted(m: boolean): void; mute(): void; unmute(): void   // capture stays open, sends silence
  devices(): Promise<MicDevice[]>
  select(deviceId: string | null): Promise<void>            // moves a live capture
  analyser(): AnalyserNode | null
  on('state', (s: MicState) => void); on('devicechange', (d: MicDevice[]) => void)
  on('devicelost', ({ deviceId }) => void)                  // chosen device unplugged → default
  on('error', ({ code: 'not-allowed' | 'no-device' | 'busy' | 'unsupported' | 'unknown', message }) => void)
  dispose(): void
}
```

On-device recognition captures audio itself. Where the browser accepts a track (`SpeechRecognition.start(track)`), the
selected microphone's track is passed to it; otherwise device selection affects the audio signals only.

### Voice

```ts
type VoiceState = 'idle' | 'starting' | 'active' | 'unsupported' | 'error'   // engine lifecycle
type VoiceMode = 'dictation' | 'conversation'

interface VoiceEngine {
  readonly kind: 'browser' | 'server'
  readonly state: VoiceState
  readonly phase: TurnPhase
  readonly signals: AudioSignals
  readonly muted: boolean
  start(): Promise<void>          // from a user gesture
  stop(): Promise<void>
  interrupt(): Promise<void>      // stop button: silence + cancel the running turn
  setMuted(muted: boolean): void
  on<K extends keyof VoiceEvents>(event: K, fn): () => void
}
interface VoiceEvents {
  state: VoiceState
  phase: TurnPhase
  interim: string                                        // partial transcript ('' when final)
  heard: string                                          // a final user phrase, after the echo guard
  segmentStart: SpokenSegment                            // { turnId, index, text }: subtitles
  boundary: { segment: SpokenSegment; charIndex: number }
  segmentEnd: SpokenSegment & { cancelled: boolean }
  bargeIn: { turnId: string | null; heard: string }
  hold: boolean                                          // the agent paused for the user (true) or carried on (false)
  mic: boolean                                           // the microphone opened or closed (strict turns, mute)
  error: VoiceError                                      // { code, message }
}
interface VoiceCommonOptions {
  locale?: LocaleCode | LocalePack   // default: the session's locale, else 'en'
  lang?: string                  // default: the locale's ('en-US', 'it-IT')
  micSound?: MicSound            // 'goccia'
  microphone?: Microphone
  bargeIn?: { enabled?: boolean; minWords?: number }   // true, 3
}

class BrowserVoiceEngine implements VoiceEngine {
  constructor(opts: VoiceCommonOptions & { session; stt?: SttEngine; tts?: TtsEngine; ttsOptions?: ChooseTtsOptions; rate?; meterMicrophone?; recognition?; restartMs?
    talkOver? = true; holdWords? = 2; holdMs? = 1600; settleMs? = 750; echoTailMs? = 4000 })
  readonly microphone: Microphone; readonly stt: SttEngine; readonly tts: TtsEngine
  readonly talkOver: boolean; readonly held: boolean; readonly micOpen: boolean
  setTalkOver(on: boolean): void      // off: strict turns, the microphone opens only when the agent has finished
}
class ServerVoiceEngine implements VoiceEngine {
  constructor(opts: Omit<VoiceCommonOptions, 'microphone'> & { session; iceServers?; loadPipecat?: () => Promise<PipecatModules>; reloadAfterTurn?; audioContext? })
}
function createVoiceEngine(session, opts?, kind = session.voiceEngine): VoiceEngine

class Dictation {                // text into the application's own input; nothing goes to the agent
  constructor(opts?: Omit<VoiceCommonOptions, 'bargeIn'> & { stt?; continuous?; meterMicrophone?; recognition? })
  readonly state: 'idle' | 'starting' | 'listening' | 'unsupported' | 'error'
  readonly signals: AudioSignals; readonly microphone: Microphone; readonly stt: SttEngine
  start(): Promise<void>; stop(): void; cancel(): void
  on('state' | 'text' | 'final' | 'error', fn)
}
```

**Browser engine**: on-device recognition only, never a cloud recogniser. It speaks the turn's `say` events in index
order through a `TtsEngine`, and sends what the user says as text with `mode: "voice"`; the server matches "yes",
"cancel" and "the second" in the session's language. Talking over the agent (`talkOver`, on by default):

- the microphone hears the agent too: for `echoTailMs` after it spoke, its own words are stripped from the start of a
  transcript (`stripEcho`) and a transcript that is mostly its words is ignored (`isEcho`);
- `holdWords` meaningful words (`meaningfulWords`: hesitations do not count) among the last ones heard **hold** the
  agent: it goes quiet and keeps what it had left, from the word it reached (the reported boundary, or an estimate of
  14 characters a second); a sound that never becomes a phrase **resumes** it after `holdMs` (`hold` events);
- `minWords` interim words are a **barge-in**: the speech stops, the turn is cancelled and the phase is `interrupted`;
- a phrase is sent when the recogniser's result is final, or when an interim result stays the same for `settleMs`
  (its final result then adds only the new words).

With `talkOver` off the turns are strict: the microphone closes (with its sound) when the agent's turn starts and
opens again when it has finished speaking, so the agent is never cut off (`mic` events; `setTalkOver` switches it at
once).

**Server engine**: Pipecat over WebRTC. The offer goes to `/v1/session/voice/offer` with the bearer token; with
external storage the history is sent as `requestData.history`. RTVI messages `{evokai: event}` are fed into the
session; the call's audio tracks are measured (`onTrackStarted`): the bot's track is the `agent` signal, the local
microphone the `user` signal. With a bundler, pass `loadPipecat: async () => pipecatModules(await import('@pipecat-ai/client-js'), await
import('@pipecat-ai/small-webrtc-transport'))`.

### Speech to text

```ts
interface SttEngine {
  readonly kind: 'on-device'; readonly lang: string; readonly running: boolean
  support(): Promise<'ready' | 'downloadable' | 'unsupported'>
  install(): Promise<boolean>
  start(opts: { continuous: boolean; track?: MediaStreamTrack | null }): Promise<void>
  stop(): void; abort(): void
  on('interim' | 'final', (text) => void); on('speechstart' | 'end', () => void); on('error', (e: VoiceError) => void)
}
class OnDeviceStt implements SttEngine { constructor(opts?: { lang? = 'en-US'; recognition?; restartMs? }) }
recognitionSupport(opts?), installRecognition(opts?), ensureRecognition(opts?)
```

### Text to speech

```ts
type TtsKind = 'piper' | 'browser' | 'server'
interface TtsStatus { state: 'idle' | 'loading' | 'ready' | 'unavailable' | 'error'; progress?: TtsProgress; cached?: boolean; error?: VoiceError }
interface TtsProgress { loaded: number; total: number | null; fraction: number | null }
interface TtsEngine {
  readonly kind: TtsKind; readonly status: TtsStatus
  readonly measurable: boolean      // audio the client can measure (Piper, server) vs estimated (speechSynthesis)
  readonly speaking: boolean
  prepare(): Promise<void>
  speak(text: string, opts?: { rate?; signal? }): TtsUtterance   // { text, done: Promise<{cancelled}>, cancel() }
  cancel(): void
  on('status', s); on('progress', p); on('ready'); on('start', { text }); on('boundary', { text, charIndex })
  on('end', { text, cancelled }); on('signal', AudioSignal)
}

class BrowserTts implements TtsEngine      // speechSynthesis, voices with localService === true only
  // options: { locale? = 'en', lang? (default the locale's), rate? = 1.05, estimate? = true, synthesis?, Utterance? }
  // Emits estimated `signal`s from the text (syllableDriver), re-synced on every word `boundary`.
class PiperTts implements TtsEngine        // in-browser neural voice (desktop)
  // options: { voice = 'it_IT-serena-high', modelUrl = '/evokai/voices/<voice>.onnx', configUrl = modelUrl + '.json',
  //            cache: 'cache-storage' (default) | 'opfs' | 'none', store?: ModelStore, loadRuntime?: () => Promise<PiperRuntime>,
  //            audioContext?, rate?, fetch? }
  readonly voice; readonly modelUrl; readonly configUrl
class DeviceTts implements TtsEngine       // primary (Piper) when ready, fallback (browser) meanwhile
  constructor(primary: TtsEngine, fallback: TtsEngine); readonly active: TtsEngine
function chooseTts(opts?: { locale?; device?: 'desktop' | 'phone' | 'auto'; piper?: PiperTtsOptions | false; browser?: BrowserTtsOptions }): TtsEngine
function detectDevice(env?: { userAgent?; maxTouchPoints?; width?; mobileHint? }): 'desktop' | 'phone'
function pickLocalVoice(voices, lang = 'en-US'): VoiceLike | null
const DEFAULT_PIPER_VOICES = { it: 'it_IT-serena-high' }; const DEFAULT_PIPER_VOICE = 'it_IT-serena-high'
const DEFAULT_VOICE_BASE = '/evokai/voices/'

// Piper runtime (optional dependencies, never in the main entry)
interface PiperRuntime { load(model: ArrayBuffer, config: PiperVoiceConfig): Promise<PiperSynth> }
interface PiperSynth { sampleRate: number; synthesize(text: string, opts?: { rate? }): Promise<Float32Array> }
function createPiperRuntime(mods: { ort: OrtLike; phonemize: (text, language?) => Promise<string[]> }): PiperRuntime
function phonemeIds(phonemes: string, map: Record<string, number[]>): number[]   // BOS, phoneme + pad…, EOS
function sentences(text: string): Array<{ text; end }>
function modelStore(kind: 'cache-storage' | 'opfs' | 'none', env?): ModelStore     // { get(key), put(key, data) }
function download(fetch, url, onProgress: (p: TtsProgress) => void): Promise<ArrayBuffer>
```

**Engine choice.** `chooseTts` picks by device and language:

| Device | Language with a Piper voice (`it`) | Other languages (`en`, …) |
| --- | --- | --- |
| Desktop | `DeviceTts`: browser voice until Piper is `ready`, then Piper | `BrowserTts` |
| Phone / tablet | `BrowserTts` | `BrowserTts` |

English has no default Piper voice: pass `piper: { voice, modelUrl }` to use one. The `server` TTS kind is reserved.

**Piper.** On first use the model is downloaded from `modelUrl`, a path your application serves (it defaults to
`/evokai/voices/`, never a third-party CDN), with `progress` events (`{loaded, total, fraction}`, plus a `status` of
`loading` carrying the same progress). It is stored in Cache Storage (`evokai-voices-v1`) or OPFS, and `ready` fires
once it is loaded. Later sessions read the cache (`status.cached === true`, no download). Show
`locales.<code>.voiceText.preparingVoice(persona)` while `status.state === 'loading'`. Text is split into sentences,
phonemised with espeak-ng (`config.espeak.voice`), turned into ids with `config.phoneme_id_map`, and run through ONNX
with the config's `noise_scale`, `length_scale / rate` and `noise_w`. The audio plays through an analyser, so its
`signal`s are measured like the microphone's. Piper gives no word timings: there are no `boundary` events while it
speaks. Default runtime loader: a computed `import()` of `onnxruntime-web` and `phonemizer`. With a bundler, pass:

```ts
loadRuntime: async () => createPiperRuntime({ ort: await import('onnxruntime-web'), phonemize: (await import('phonemizer')).phonemize })
```

### Audio signals

```ts
interface AudioSignal {
  source: 'user' | 'agent'
  rms: number               // RMS × 3.2, clamped 0–1: same scale for every source
  level: number             // 0.1 + rms × 0.85, capped at 0.9
  onset: boolean            // a syllable starts (RMS jump > 0.04, or an estimated syllable)
  intensity: number         // strength of that syllable 0–1 (stressed stronger), 0 when no onset
  spectrum: Float32Array | null   // 48 bins 0–1; null when estimated from text
  estimated: boolean        // true for speechSynthesis (from text), false when measured from audio
  time: number              // performance.now()
}
class AudioSignals { current(source?): AudioSignal | null; subscribe(fn): () => void; push(s): void; silence(source): void }
function measure(an: AnalyserLike, prevRms: number, buffers?): { rms; level; onset; spectrum }
function meterAnalyser(an, source, signals): () => void   // publishes every animation frame
const SPECTRUM_BINS = 48, RMS_GAIN = 3.2, ONSET_JUMP = 0.04

// Estimation from text, for speechSynthesis (no audio to read):
function syllableRules(locale?): SyllableRules | null        // it: full rules; en: rough fallback; others: null (no estimate)
function syllabify(word: string, locale = 'en'): string[]     // syllabify('settembre', 'it') → ['set', 'tem', 'bre']
function stressIndex(syllables: string[], locale = 'en'): number   // it: the accented vowel, else the penultimate; en: the first
function timeline(text: string, opts?: { from?; rate?; locale?; random? }): { events: Array<{ at; intensity; word; char }>; duration }
function syllableDriver(text: string, opts: { emit: (s: AudioSignal) => void; rate?; locale?; random?; now?; raf? }): { resync(charIndex): void; stop(): void }
const SYLLABLE_SECONDS = 0.165, DRIVER_DECAY = 0.93
```

**Estimate rules.** A syllable lasts 0.165 s ÷ rate (15 % longer above three letters). A comma, semicolon or colon
adds 0.22 s ÷ rate, a full stop 0.42 s ÷ rate, and any other word gap 0.03 s. Intensity is 1 for the stressed
syllable and 0.55 otherwise, × 0.6 for function words; at a sentence end it is multiplied by 0.75 + 0.25 × (1 − k/n),
then jittered ±15 %. The driver fires each syllable as an onset: `level = rms = 0.12 + 0.7 × intensity`, decaying
× 0.93 per frame, `estimated: true`, `spectrum: null`. On each `boundary` it jumps to the first syllable of that word.
Italian syllables: a vowel nucleus (unstressed i/u join the next vowel), one consonant opens the next syllable,
doubled consonants split, "s" + consonant and the pairs bl br cl cr dr fl fr gl gr pl pr tr vr ch gh gn sc qu gu stay
together, and other clusters split after the first consonant. English is a rough vowel-group fallback with stress on
the first syllable. Languages without rules give **no estimate**: the timeline is empty and the driver publishes
nothing.

### Microphone sounds

```ts
type MicSound = 'goccia' | 'classico' | 'vetro' | 'respiro' | 'cristallo' | 'clic' | 'none' | { on: string; off: string }
function playMicSound(sound: MicSound, which: 'on' | 'off', opts?: { context?: AudioContextLike; volume? = 1; fetch? }): Promise<void>
const MIC_SOUNDS, MIC_SOUND_RECIPES, DEFAULT_MIC_SOUND = 'goccia'
const CLASSICO_URLS: { on(): string; off(): string }   // new URL('./sounds/classico-<on|off>.mp3', import.meta.url)
```

The identifiers are fixed (they are Italian words; English display names are in `locales.en.micSounds`):

| Id | Name | Recipe (open; close reverses it) | Gain |
| --- | --- | --- | --- |
| `goccia` | Drop | sine 988 Hz 0–0.14 s + 1319 Hz 0.08–0.22 s | 0.18 |
| `classico` | Classic | two packaged MP3 files (mono, 64 kbit/s, ~22 KB together) | 0.9 |
| `vetro` | Glass | FM bell: modulator × 3.5, index 2.2 decaying; 1568 Hz 0.6 s (close 1046 Hz 0.5 s) | 0.12 |
| `respiro` | Breath | band-pass (Q 6) white noise 0.35 s sweeping 500 → 2600 Hz | 0.5 |
| `cristallo` | Crystal | triangle 880 / 1109 / 1319 Hz, 45 ms apart, 0.32 s (close 0.3 s) | 0.11 |
| `clic` | Soft click | sine glide 600 → 1200 Hz, 0.09 s | 0.22 |

Notes have an 8 ms linear attack (the bell 4 ms) and an exponential decay to 0.0008. Engines play the open sound
once listening starts and the close sound when they stop. A suspended context is resumed first, so call from a user
gesture.

### Locales

```ts
type LocaleCode = 'en' | 'it'
interface LocalePack {
  code; lang                                        // 'en' 'en-US' | 'it' 'it-IT'
  speech: { yes; no; ordinals; sources; abbreviations; redactions: { taxCode; iban; vat; email; phone } }
  voiceText: VoiceText
  micSounds: Record<MicSoundName, { name; description }>
}
interface VoiceText {
  preparingVoice(persona?: string): string   // en: "Preparing the voice: this only happens the first time"
  voiceReady(persona?: string): string
  recognitionUnsupported; recognitionDownload; micDenied; micMissing: string
}
const locales: { en: LocalePack; it: LocalePack }
function localePack(locale?: LocaleCode | LocalePack | string): LocalePack   // unknown codes → en
```

`locale` is an option of `EvokaiSession` (default `'en'`). The engines, `Dictation` and `chooseTts` use the session's
locale unless they get their own. It selects the recognition and voice language, the speech rules (`speakable`,
`SentenceSplitter`, `spokenDecision`, `spokenOption` take an optional locale) and the syllable rules. In
`spokenOption` the ordinal said first wins ("the second one" is 2), as on the server. A custom
`LocalePack` works anywhere a code does. The speech word lists mirror the server's locale packs
(`src/evokai/locales/<code>.json`).

---

## 4. `/react` exports

```ts
<EvokaiProvider session? options? voice?: VoiceEngine | CreateVoiceOptions>   // context only
useEvokaiSession(session?) → { session, state }
usePhase(session?) → TurnPhase
useUsage(session?) → UsageShares | null
useVoice(engine?) → { engine, state, phase, interim, error, muted, active, start, stop, interrupt, setMuted }
useMicrophone(engineOrDictation) → MicState | null
useAudioSignals(signals, { source?, fps = 30 }) → AudioSignal | null   // per-frame visuals: use signals.subscribe
useDictation(opts?) → { dictation, state, text, start, stop, cancel }
```

---

## 5. Wiring your own interface

```ts
import { BrowserVoiceEngine, EvokaiSession, createVoiceEngine, isLimitError, locales } from '@evokai/evokai-client/browser'

const session = new EvokaiSession({
  baseUrl: 'https://agents.example.com/agent',
  locale: 'en',
  getToken: ({ conversationId }) =>
    fetch('/api/agent/token', { method: 'POST', body: JSON.stringify({ conversationId }), headers: { 'Content-Type': 'application/json' } }).then((r) => r.json()),
})

// Messages and cards from state; motion from the phase; triggers from events.
session.subscribe((s) => ui.renderMessages(s.messages))
session.on('phase', (e) => ui.setPhase(e.phase))               // idle | listening | thinking | speaking | interrupted
session.on('confirm', (e) => ui.confirmCard(e.items, (ok) => session.confirm(e.actionId, ok)))
session.on('ask', (e) => ui.options(e.options, (picked) => session.answer(e.askId, picked)))
session.on('navigate', (e) => router.push(e.path))
session.on('error', (e) => (isLimitError(e) ? ui.limitReached(e.limit, e.message) : ui.toast(e.message)))
session.subscribeUsage((u) => ui.usage(u.dayPct, u.monthOut))

ui.onSend((text) => session.chat({ text }))
ui.onStop(() => session.cancel())

// Voice.
const voice = createVoiceEngine(session, { micSound: 'goccia' })
ui.onVoiceButton(() => (voice.state === 'idle' ? voice.start() : voice.stop()))
ui.onMuteButton((m) => voice.setMuted(m))
voice.on('interim', (t) => ui.liveTranscript(t))
voice.on('segmentStart', (seg) => ui.subtitle(seg.text))
voice.on('boundary', ({ charIndex }) => ui.subtitleProgress(charIndex))

// The in-browser voice downloads once (desktop, Italian): show a line meanwhile.
if (voice instanceof BrowserVoiceEngine) {
  voice.tts.on('status', (st) => ui.voiceNote(st.state === 'loading' ? locales.en.voiceText.preparingVoice('Ada') : null))
  voice.tts.on('progress', (p) => ui.voiceProgress(p.fraction))
}

// Any visual: read the latest signal in your own animation loop.
function frame() {
  const s = voice.signals.current()
  orb.draw({ level: s?.level ?? 0, onset: s?.onset ?? false, spectrum: s?.spectrum, from: s?.source })
  requestAnimationFrame(frame)
}
requestAnimationFrame(frame)
```

---

## 6. Licences

The package is Apache-2.0 (`LICENSE` and `NOTICE` ship with it). Everything below is optional. None of it is
bundled into any entry point: the Piper runtime is loaded with a dynamic `import()`, and the voice model is served by
your application.

| Component | Used for | Licence | Notes |
| --- | --- | --- | --- |
| `onnxruntime-web` | Piper inference | MIT | Optional dependency (`loadRuntime`). |
| `phonemizer` (npm) | espeak-ng phonemes for Piper | Declares Apache-2.0, but **embeds espeak-ng compiled to WASM, which is GPL-3.0-or-later** | Treat it as GPL-3.0. Distributing an application that bundles it brings the GPL's obligations. Keep it out of your main bundle; load it on demand (a separate chunk) or replace the phonemizer. |
| espeak-ng | Phonemization (inside `phonemizer`) | GPL-3.0-or-later | See above. |
| Piper (rhasspy/piper) | Model format and phoneme-id scheme | MIT | No Piper code is included: the client reimplements the id mapping and the ONNX call. |
| Voice `it_IT-serena-high` | Italian neural voice | Trained on the synthetic dataset `committa/serena-synthetic-it-27h`, **CC-BY-4.0** (model card) | Attribution required: credit the dataset when you ship the voice. The voices repository is published under MIT. |
| `@pipecat-ai/client-js`, `@pipecat-ai/small-webrtc-transport` | Server voice | BSD-2-Clause | Optional peer dependencies. |
| `classico` sound files | Microphone sound | From the reference mock-ups | Provenance to be confirmed by the owner. The synthesised sounds are original code. |
