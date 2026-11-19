/**
 * Audio unlock: browsers refuse to play sound a page did not start from a user gesture. Speech synthesis then fails
 * silently (`not-allowed`) and an AudioContext stays suspended, while the microphone keeps working: the assistant
 * hears but is never heard. Safari and every iOS browser also require the first sound to start inside the gesture
 * itself, before any `await`. Call `unlockAudio()` first thing in the click or tap that starts voice mode.
 */
import { sharedContext } from './mic-sounds'

export interface UnlockTargets {
  synthesis?: { cancel(): void; speak(u: unknown): void; resume?(): void } | null
  Utterance?: new (text: string) => { volume: number }
  context?: { state?: string; resume?(): Promise<void>; createBuffer?(c: number, l: number, r: number): unknown; createBufferSource?(): { buffer: unknown; connect(d: unknown): void; start(t?: number): void }; destination?: unknown } | null
}

/** Unlock speech synthesis and the shared AudioContext from a user gesture (safe to call more than once). */
export function unlockAudio(t: UnlockTargets = {}): void {
  const synth = t.synthesis !== undefined ? t.synthesis : typeof window !== 'undefined' ? (window.speechSynthesis ?? null) : null
  const Utt = t.Utterance ?? (typeof SpeechSynthesisUtterance !== 'undefined' ? SpeechSynthesisUtterance : undefined)
  try {
    if (synth && Utt) {
      // Chrome on macOS can keep a paused or stuck queue from an earlier page: start clean, then speak a silent
      // utterance inside the gesture so later sentences are allowed.
      synth.cancel()
      synth.resume?.()
      const u = new Utt(' ')
      u.volume = 0
      synth.speak(u)
    }
  } catch {
    /* no speech synthesis: nothing to unlock */
  }
  try {
    const ctx = t.context !== undefined ? t.context : (sharedContext() as unknown as UnlockTargets['context'])
    if (ctx) {
      if (ctx.state === 'suspended') void ctx.resume?.().catch(() => undefined)
      // iOS also wants a sound started in the gesture: one silent sample.
      if (ctx.createBuffer && ctx.createBufferSource) {
        const src = ctx.createBufferSource()
        src.buffer = ctx.createBuffer(1, 1, 22050)
        src.connect(ctx.destination)
        src.start(0)
      }
    }
  } catch {
    /* no Web Audio: nothing to unlock */
  }
}
