/**
 * The mascot's listening style: it looks up a little towards the person, the hat tip leans to them, and on each pause
 * it nods ("I'm following"). Syllables and pauses come from the microphone's level.
 */

/** How long a nod lasts (seconds). */
export const NOD_S = 0.45

/** How far into a nod the mascot is: 0, up to 1 halfway, back to 0, over `NOD_S` after the pause. */
export function nodDip(since: number): number {
  return since >= 0 && since < NOD_S ? Math.sin((Math.PI * since) / NOD_S) : 0
}

/**
 * Syllables and pauses from the microphone's level (0..1), one reading per frame: a syllable is a rise above the voice
 * threshold, a pause is 0.35 s below it after speech.
 */
export class Listener {
  private prev = 0
  private lastSyllable = -9
  private voicedUntil = -9
  private paused = true

  constructor(private readonly on: { onSyllable: () => void; onPause: () => void }) {}

  step(level: number, now: number): void {
    if (level > 0.32) this.voicedUntil = now
    if (level > 0.3 && level - this.prev > 0.025 && now - this.lastSyllable > 0.14) {
      this.lastSyllable = now
      this.on.onSyllable()
    }
    if (now - this.voicedUntil > 0.35 && !this.paused) {
      this.paused = true
      this.on.onPause()
    }
    if (now - this.voicedUntil < 0.05) this.paused = false
    this.prev = level
  }
}
