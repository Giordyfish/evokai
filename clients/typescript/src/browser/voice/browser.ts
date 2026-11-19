/**
 * Voice conversation on the device: on-device recognition (`SttEngine`), a device-chosen voice (`TtsEngine`), the
 * microphone, and audio signals. The agent's words are the turn's `say` events (cut and redacted by the server),
 * spoken in index order; spoken answers ("yes", "the second") go as text with mode "voice" and the server matches
 * them. A `say` with a `spoken` form (numbers in words) is spoken in that form, while every event to the page
 * (`segmentStart`, `boundary`, `segmentEnd`) carries the shown `text`, digits and all.
 *
 * Talking over the agent, when `talkOver` is on (the default):
 * - the microphone also hears the agent: its own words are stripped from the start of a transcript and an echo is
 *   ignored (for `echoTailMs` after it last spoke);
 * - one meaningful word of the user **holds** the agent: it goes quiet but keeps what it had left to say, from the
 *   word it reached; if that sound never becomes a phrase it **resumes** after `holdMs`;
 * - a phrase (the recogniser's final result, or an interim one unchanged for `settleMs`), or `minWords` interim words,
 *   is a **barge-in**: the speech stops, the turn is cancelled and the phrase is sent.
 *
 * With `talkOver` off the turns are strict: the microphone closes while the agent thinks and speaks, and opens again
 * (with its sound) when it has finished, so the agent can never be cut off.
 */
import type { TurnPhase } from '../../core/types'
import { playMicSound } from '../audio/mic-sounds'
import { AudioSignals, meterAnalyser } from '../audio/signals'
import { Microphone } from '../mic/microphone'
import type { EvokaiSession } from '../session'
import { isEcho, isTrailingEcho, meaningfulWords, spokenDecision, stripEcho, tailWords } from '../speech'
import { OnDeviceStt, type OnDeviceSttOptions } from '../stt/on-device'
import { chooseTts, type ChooseTtsOptions } from '../tts/device'
import { localePack, type LocalePack } from '../../locales'
import {
  Emitter,
  type SpokenSegment,
  type SttEngine,
  type TtsEngine,
  type VoiceCommonOptions,
  type VoiceEngine,
  type VoiceEvents,
  type VoiceState,
} from './types'

/** A sentence to say: what is shown, and what the voice says (the same text unless numbers are spelled out). */
interface Line {
  text: string
  spoken: string
}

/** Both forms of a line, for the echo guard: the microphone hears the words, a transcript may write the digits. */
const forms = (l: Line): string => (l.spoken === l.text ? l.text : `${l.text} ${l.spoken}`)

/** A character position in the spoken form, mapped proportionally onto the shown text. */
const shownAt = (l: Line, charIndex: number): number =>
  l.spoken === l.text ? charIndex : Math.min(l.text.length, Math.max(0, Math.round((charIndex * l.text.length) / Math.max(1, l.spoken.length))))

export interface BrowserVoiceOptions extends VoiceCommonOptions, Pick<OnDeviceSttOptions, 'recognition' | 'restartMs'> {
  session: EvokaiSession
  /**
   * Where the device has no on-device recogniser (Safari, every iOS browser), use the browser's online one (default
   * false): the audio goes to the browser vendor's speech service. On-device recognition always comes first.
   */
  allowOnlineRecognition?: boolean
  /** Speech recognition (default: `OnDeviceStt`). */
  stt?: SttEngine
  /** Speech synthesis (default: `chooseTts(tts)`: Piper on desktop, local browser voices on phones). */
  tts?: TtsEngine
  /** Options for the default TTS choice. */
  ttsOptions?: ChooseTtsOptions
  /** Speech rate. */
  rate?: number
  /** Capture the microphone for the user's audio signal (default true; recognition has its own capture). */
  meterMicrophone?: boolean
  /** The user may talk over the agent (default true). Off: strict turns. */
  talkOver?: boolean
  /** Meaningful words, among the last ones heard, that hold the agent (default 1: the first word heard quiets it; a hold is undone if no phrase follows). */
  holdWords?: number
  /** A hold that never became a phrase resumes after this long (default 1600 ms). */
  holdMs?: number
  /** An interim phrase unchanged for this long is taken as said (default 750 ms; 0: wait for the final result). */
  settleMs?: number
  /** For this long after the agent spoke, its words are stripped from what the microphone heard (default 4000 ms). */
  echoTailMs?: number
}

/** Characters per second at rate 1, to estimate where a voice without word boundaries had got to. */
const CHARS_PER_SECOND = 14

export class BrowserVoiceEngine implements VoiceEngine {
  readonly kind = 'browser' as const
  readonly signals = new AudioSignals()
  readonly microphone: Microphone
  readonly stt: SttEngine
  readonly tts: TtsEngine
  private current: VoiceState = 'idle'
  private readonly events = new Emitter<VoiceEvents>()
  private readonly session: EvokaiSession
  private readonly opts: BrowserVoiceOptions
  private readonly pack: LocalePack
  private active = false
  private unsubs: Array<() => void> = []
  private stopMeter: (() => void) | null = null
  private interrupt_: boolean
  private micOpen_ = false
  // speaking
  private speakTurn: string | null = null
  private nextIndex = 0
  private readonly waiting = new Map<number, Line>()
  private pending = 0
  private spokenTurn = ''
  private readonly muted_ = new Set<string>()
  /** Lines handed to the TTS and not finished, in order (the first is playing). */
  private queue: Line[] = []
  /** The line playing: `at` is the position reached in its spoken form, `shown` the same in its shown text. */
  private playing: { line: Line; startedAt: number; at: number; shown: number } | null = null
  private spokeAt = 0
  // holding and listening
  private stash: Line[] | null = null
  private resumeTimer: ReturnType<typeof setTimeout> | null = null
  private settleTimer: ReturnType<typeof setTimeout> | null = null
  /** What was already sent of the phrase being recognised (so its final result sends only what is new). */
  private committed = ''

  constructor(opts: BrowserVoiceOptions) {
    this.session = opts.session
    this.opts = opts
    this.interrupt_ = opts.talkOver !== false
    this.microphone = opts.microphone ?? new Microphone()
    this.pack = localePack(opts.locale ?? opts.session.locale)
    const lang = opts.lang ?? this.pack.lang
    this.stt = opts.stt ?? new OnDeviceStt({ lang, recognition: opts.recognition, restartMs: opts.restartMs, allowOnline: opts.allowOnlineRecognition })
    this.tts = opts.tts ?? chooseTts({ locale: this.pack, ...opts.ttsOptions, browser: { lang, ...opts.ttsOptions?.browser } })
  }

  get state(): VoiceState {
    return this.current
  }

  get phase(): TurnPhase {
    return this.session.state.phase
  }

  get muted(): boolean {
    return this.microphone.state.muted
  }

  /** Whether the user may talk over the agent (else strict turns). */
  get talkOver(): boolean {
    return this.interrupt_
  }

  /** The agent is holding (quiet, waiting to see whether the user is talking). */
  get held(): boolean {
    return this.stash !== null
  }

  /** The microphone is listening (closed while muted, and during the agent's turn in strict turns). */
  get micOpen(): boolean {
    return this.micOpen_
  }

  on<K extends keyof VoiceEvents>(event: K, fn: (value: VoiceEvents[K]) => void): () => void {
    return this.events.on(event, fn)
  }

  private set(state: VoiceState): void {
    if (state === this.current) return
    this.current = state
    this.events.emit('state', state)
  }

  private fail(code: string, message: string, state: VoiceState = 'error'): void {
    this.set(state)
    this.events.emit('error', { code, message })
  }

  /** Start a voice conversation (from a user gesture: it may download the language pack and the voice). */
  async start(): Promise<void> {
    if (this.active) return
    this.set('starting')
    if ((await this.stt.support()) === 'unsupported') return this.fail('unsupported', 'On-device speech recognition is not available.', 'unsupported')
    this.active = true
    void this.tts.prepare().catch(() => undefined)
    this.unsubs = [
      this.session.on('phase', (e) => this.events.emit('phase', e.phase)),
      this.session.on('conversation', (e) => {
        if (e.turnStarted && e.turnId) this.beginTurn(e.turnId)
        if (e.turnStarted) this.agentsTurn()
      }),
      this.session.on('say', (e) => this.onSay(e.turnId ?? null, e.index, { text: e.text, spoken: e.spoken || e.text })),
      this.session.on('done', () => this.settle()),
      this.stt.on('interim', (t) => this.onInterim(t)),
      this.stt.on('final', (t) => this.onFinal(t)),
      this.stt.on('error', (e) => {
        void this.stop().then(() => this.fail(e.code, e.message))
      }),
      this.tts.on('start', ({ text }) => {
        const line = this.lineOf(text)
        this.playing = { line, startedAt: Date.now(), at: 0, shown: 0 }
        this.spokeAt = Date.now()
        this.events.emit('segmentStart', this.segment(line))
      }),
      this.tts.on('boundary', ({ text, charIndex }) => {
        const p = this.playing?.line.spoken === text ? this.playing : null
        const line = p?.line ?? this.lineOf(text)
        let shown = shownAt(line, charIndex)
        if (p) {
          // The voice reports positions in what it says; the page gets them in what it shows, never going back.
          p.at = charIndex
          shown = p.shown = Math.max(p.shown, shown)
        }
        this.events.emit('boundary', { segment: this.segment(line), charIndex: shown })
      }),
      this.tts.on('signal', (s) => this.signals.push(s)),
    ]
    try {
      if (this.opts.meterMicrophone !== false) {
        await this.microphone.start().catch(() => undefined)
        const an = this.microphone.analyser()
        if (an) this.stopMeter = meterAnalyser(an, 'user', this.signals)
      }
      await this.listen()
    } catch {
      this.active = false
      return
    }
    await playMicSound(this.opts.micSound ?? 'goccia', 'on').catch(() => undefined)
    this.set('active')
    this.session.setVoiceActivity('listening')
  }

  async stop(): Promise<void> {
    const was = this.active
    this.active = false
    for (const u of this.unsubs) u()
    this.unsubs = []
    this.clearTimers()
    this.stash = null
    this.stopSpeaking()
    this.closeMic()
    this.stopMeter?.()
    this.stopMeter = null
    if (!this.opts.microphone) this.microphone.stop()
    this.session.setVoiceActivity(null)
    if (was) await playMicSound(this.opts.micSound ?? 'goccia', 'off').catch(() => undefined)
    this.set('idle')
  }

  async interrupt(): Promise<void> {
    this.bargeIn('')
  }

  setMuted(muted: boolean): void {
    this.microphone.setMuted(muted)
    if (!this.active) return
    if (muted) this.closeMic()
    else if (this.interrupt_ || !this.agentBusy()) void this.listen().catch(() => undefined)
  }

  /** Strict turns (`false`) or talking over the agent (`true`); takes effect at once. */
  setTalkOver(on: boolean): void {
    this.interrupt_ = on
    if (!this.active || this.muted) return
    if (!on && this.agentBusy()) {
      this.closeMic()
      void playMicSound(this.opts.micSound ?? 'goccia', 'off').catch(() => undefined)
    } else if (on && !this.micOpen_) {
      void playMicSound(this.opts.micSound ?? 'goccia', 'on').catch(() => undefined)
      void this.listen().catch(() => undefined)
    }
  }

  // ── listening ──────────────────────────────────────────────────────────────────────────────────────────────

  private async listen(): Promise<void> {
    if (!this.active && this.current !== 'starting') return
    this.committed = ''
    await this.stt.start({ continuous: true, track: this.microphone.track })
    if (!this.micOpen_) {
      this.micOpen_ = true
      this.events.emit('mic', true)
    }
  }

  private closeMic(): void {
    this.clearSettle()
    this.stt.abort()
    if (this.micOpen_) {
      this.micOpen_ = false
      this.events.emit('mic', false)
    }
  }

  private agentBusy(): boolean {
    return this.session.state.busy || this.tts.speaking || this.pending > 0
  }

  /** A turn of the agent began: in strict turns the microphone closes until it has finished. */
  private agentsTurn(): void {
    if (!this.active || this.interrupt_ || !this.micOpen_) return
    this.closeMic()
    void playMicSound(this.opts.micSound ?? 'goccia', 'off').catch(() => undefined)
  }

  /** What the agent said recently, is saying or is about to say (to tell its voice from the user's). */
  private agentsWords(): string {
    return [this.spokenTurn, ...this.queue.map(forms), ...(this.stash ?? []).map(forms)].join(' ')
  }

  private recent(): boolean {
    return this.tts.speaking || this.held || Date.now() - this.spokeAt < (this.opts.echoTailMs ?? 4000)
  }

  /** The user's part of a transcript, and whether it is someone talking (not echo, not a hesitation). */
  private read(raw: string): { mine: string; talking: boolean; echo: boolean } {
    const hers = this.agentsWords()
    // While it speaks (or holds), the microphone hears it: its words are stripped. Once it has finished, only a late
    // transcript of its own phrase is echo; a short reply that reuses its words is the user's and is sent.
    const live = this.tts.speaking || this.held
    const count = (t: string) => t.trim().split(/\s+/).filter(Boolean).length
    const stripped = live || this.recent() ? stripEcho(raw, hers) : raw.trim()
    // Just after it finished, a transcript may still start with its phrase: that is cut only when it is a real run of
    // its words (three or more), never one or two words the user happens to share with it.
    const mine = live || count(raw) - count(stripped) >= 3 ? stripped : raw.trim()
    const echo = !mine.trim() || (live ? isEcho(mine, hers) : this.recent() && isTrailingEcho(mine, hers))
    const talking = !echo && (meaningfulWords(mine, this.pack).length > 0 || spokenDecision(mine, this.pack) !== null)
    return { mine, talking, echo }
  }

  private onInterim(raw: string): void {
    if (!raw.trim()) return this.events.emit('interim', '')
    const { mine, talking, echo } = this.read(raw)
    this.events.emit('interim', echo ? '' : mine)
    if (!this.interrupt_ && this.agentBusy()) return
    this.clearSettle()
    const speaking = this.tts.speaking && !this.held
    const bargeIn = this.opts.bargeIn ?? {}
    if (speaking && bargeIn.enabled !== false && !echo) {
      const words = mine.trim().split(/\s+/).filter(Boolean)
      const tail = tailWords(mine)
      // Enough words: the agent stops and its turn is cancelled; the phrase goes when it is complete.
      if (words.length >= (bargeIn.minWords ?? 3)) this.bargeIn(mine)
      else if (meaningfulWords(tail, this.pack).length >= (this.opts.holdWords ?? 1) && !isEcho(tail, this.agentsWords())) this.hold()
    }
    if (!talking) return
    if (this.held) this.armResume()
    const settle = this.opts.settleMs ?? 750
    if (settle > 0 && (!this.tts.speaking || this.held)) this.settleTimer = setTimeout(() => this.commit(mine), settle)
  }

  private onFinal(raw: string): void {
    this.clearSettle()
    const { mine, talking } = this.read(raw)
    const fresh = this.fresh(mine)
    this.committed = ''
    if (!fresh.trim()) return
    // While the agent speaks, noise, hesitations and its own echo are not an interruption: it carries on.
    if (this.tts.speaking && !this.held && !talking) return
    if (!talking && this.held) return this.resume()
    if (!talking && (this.tts.speaking || this.held ? isEcho(fresh, this.agentsWords()) : this.recent() && isTrailingEcho(fresh, this.agentsWords()))) return
    this.send(fresh)
  }

  /** The words of a phrase not sent yet (an interim that settled was sent already). */
  private fresh(text: string): string {
    if (!this.committed) return text
    const before = this.committed.trim().split(/\s+/).length
    return text.trim().split(/\s+/).slice(before).join(' ')
  }

  private commit(text: string): void {
    this.clearSettle()
    const fresh = this.fresh(text)
    this.committed = text
    if (fresh.trim()) this.send(fresh)
  }

  private send(said: string): void {
    this.clearResume()
    this.stash = null
    this.events.emit('heard', said)
    if (this.tts.speaking || this.pending > 0 || this.session.state.busy) this.bargeIn(said)
    if (!this.interrupt_) this.closeMic()
    void this.session.chat({ text: said, mode: 'voice', endOfSpeechAt: Date.now() / 1000 })
  }

  /** Silence at once, mark the phase interrupted, and cancel the turn if it is still running. */
  private bargeIn(heard: string): void {
    const turn = this.speakTurn
    this.clearResume()
    this.stash = null
    this.stopSpeaking()
    const running = this.session.state.currentTurn?.turnId ?? null
    if (turn) this.muted_.add(turn)
    if (running) {
      this.muted_.add(running)
      void this.session.cancel(running).catch(() => false)
    }
    this.session.markInterrupted()
    this.events.emit('bargeIn', { turnId: running ?? turn, heard })
  }

  /** Go quiet but keep what was left to say, from the word reached. */
  private hold(): void {
    if (this.held || !this.tts.speaking) return
    const p = this.playing
    let rest: Line[] = [...this.queue]
    if (p && rest[0] === p.line) {
      // The position is in the spoken form: the rest is cut from it (a cut line shows the words it says).
      const said = p.line.spoken
      const rate = this.opts.rate ?? 1
      const estimated = Math.floor(((Date.now() - p.startedAt) / 1000) * CHARS_PER_SECOND * rate)
      const pos = Math.min(said.length, Math.max(p.at, estimated))
      const word = said.lastIndexOf(' ', Math.max(0, pos - 1)) + 1
      const left = said.slice(word).trim()
      const line = word === 0 ? p.line : { text: left, spoken: left }
      rest = [...(left ? [line] : []), ...rest.slice(1)]
    }
    this.stash = rest
    this.stopSpeaking()
    // Quiet while the user talks over it: the phase leaves «speaking» (listening, marked interrupted) until it resumes.
    this.session.markInterrupted()
    this.events.emit('hold', true)
    this.armResume()
  }

  private resume(): void {
    this.clearResume()
    const stash = this.stash
    this.stash = null
    if (stash === null) return
    this.events.emit('hold', false)
    for (const line of stash) this.say(line)
    if (!stash.length) this.settle()
  }

  private armResume(): void {
    this.clearResume()
    this.resumeTimer = setTimeout(() => this.resume(), this.opts.holdMs ?? 1600)
  }

  private clearResume(): void {
    if (this.resumeTimer) clearTimeout(this.resumeTimer)
    this.resumeTimer = null
  }

  private clearSettle(): void {
    if (this.settleTimer) clearTimeout(this.settleTimer)
    this.settleTimer = null
  }

  private clearTimers(): void {
    this.clearResume()
    this.clearSettle()
  }

  // ── speaking ───────────────────────────────────────────────────────────────────────────────────────────────

  private segment(line: Line): SpokenSegment {
    return { turnId: this.speakTurn, index: Math.max(0, this.nextIndex - this.pending), ...this.shown(line) }
  }

  /** What a segment event carries: the shown text, and the spoken form only when it differs. */
  private shown(line: Line): Pick<SpokenSegment, 'text' | 'spoken'> {
    return line.spoken === line.text ? { text: line.text } : { text: line.text, spoken: line.spoken }
  }

  /** The queued line the TTS reports by its spoken text. */
  private lineOf(spoken: string): Line {
    return this.queue.find((l) => l.spoken === spoken) ?? { text: spoken, spoken }
  }

  private beginTurn(turnId: string): void {
    if (this.speakTurn === turnId) return
    if (this.speakTurn) this.stopSpeaking()
    this.speakTurn = turnId
    this.nextIndex = 0
    this.waiting.clear()
    this.spokenTurn = ''
  }

  private onSay(turnId: string | null, index: number, line: Line): void {
    if (!this.active) return
    if (turnId && this.muted_.has(turnId)) return
    if (turnId && turnId !== this.speakTurn) this.beginTurn(turnId)
    if (index < this.nextIndex || this.waiting.has(index)) return
    this.waiting.set(index, line)
    // Deterministic: speak index 0, 1, 2… whatever order they arrive in.
    while (this.waiting.has(this.nextIndex)) {
      const t = this.waiting.get(this.nextIndex) as Line
      this.waiting.delete(this.nextIndex)
      this.nextIndex++
      // While holding, new sentences wait with the rest.
      if (this.stash) this.stash.push(t)
      else this.say(t)
    }
  }

  private say(line: Line): void {
    const seg: SpokenSegment = { turnId: this.speakTurn, index: this.nextIndex - 1, ...this.shown(line) }
    this.spokenTurn += ` ${forms(line)}`
    this.pending++
    this.queue.push(line)
    this.session.setVoiceActivity('speaking')
    const u = this.tts.speak(line.spoken, this.opts.rate ? { rate: this.opts.rate } : {})
    void u.done.then(({ cancelled }) => {
      const i = this.queue.indexOf(line)
      if (i >= 0) this.queue.splice(i, 1)
      if (this.playing?.line === line) this.playing = null
      this.spokeAt = Date.now()
      this.pending = Math.max(0, this.pending - 1)
      this.events.emit('segmentEnd', { ...seg, cancelled })
      if (this.pending === 0) this.settle()
    })
  }

  private stopSpeaking(): void {
    this.waiting.clear()
    this.tts.cancel()
    this.queue = []
    this.playing = null
    this.pending = 0
  }

  /** After speech or a turn ends: back to listening (in strict turns the microphone opens again). */
  private settle(): void {
    if (!this.active || this.held || this.tts.speaking || this.pending > 0) return
    this.session.setVoiceActivity('listening')
    if (!this.interrupt_ && !this.micOpen_ && !this.muted && !this.session.state.busy) {
      void playMicSound(this.opts.micSound ?? 'goccia', 'on').catch(() => undefined)
      void this.listen().catch(() => undefined)
    }
  }
}
