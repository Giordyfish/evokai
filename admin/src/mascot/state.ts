/**
 * What the mascot shows for each state of the assistant's chat (`data-state` of `<evokai-chat>`):
 *
 * | Chat state | Mascot |
 * |---|---|
 * | idle | the idle loop |
 * | (panel opened) | the hop greeting, once |
 * | thinking | the ember loop |
 * | tool | the rune circle |
 * | confirm | the dark flame |
 * | a change applied (after a confirmation) | the staff sparkles, once |
 * | listening | live: the hat tip perks, runes appear per syllable and fly into the hat on a pause |
 * | speaking | live: the eye light follows the voice |
 */
import type { ChatState } from '@ui/chatstate'

export type MascotView = { kind: 'clip'; name: MascotClip; loop: boolean } | { kind: 'live'; mode: 'listen' | 'speak' }

export const MASCOT_CLIPS = ['idle-combo', 'spell-hop', 'idle-ember', 'spell-rune-circle', 'spell-dark-flame', 'spell-staff-sparkles'] as const
export type MascotClip = (typeof MASCOT_CLIPS)[number]

/** How long each one-shot clip lasts (ms), from the animations' own timing. */
export const CLIP_MS: Record<MascotClip, number> = {
  'idle-combo': 4000,
  'spell-hop': 1800,
  'idle-ember': 3200,
  'spell-rune-circle': 3200,
  'spell-dark-flame': 3000,
  'spell-staff-sparkles': 2600,
}

const clip = (name: MascotClip, loop: boolean): MascotView => ({ kind: 'clip', name, loop })

/**
 * The view for a chat state, given the one before it. `greeting`: the panel just opened. `applied`: a confirmed change
 * just ran (the turn went on after the person approved).
 */
export function mascotFor(state: ChatState, previous: ChatState, opts: { greeting?: boolean; applied?: boolean } = {}): MascotView {
  if (opts.applied || (state === 'done' && previous === 'confirm')) return clip('spell-staff-sparkles', false)
  switch (state) {
    case 'listening':
      return { kind: 'live', mode: 'listen' }
    case 'speaking':
      return { kind: 'live', mode: 'speak' }
    case 'thinking':
      return clip('idle-ember', true)
    case 'tool':
      return clip('spell-rune-circle', true)
    case 'confirm':
      return clip('spell-dark-flame', true)
    default:
      return opts.greeting ? clip('spell-hop', false) : clip('idle-combo', true)
  }
}

/** The clip's file at a display size (the 256 px files above 130 px). */
export const clipSrc = (name: MascotClip, size: number): string => `fx/${name}-${size > 130 ? 256 : 128}.webp`

/** The still frame shown for reduced motion or the "still" preference. */
export const stillFor = (size: number): string => `fx/still-${size > 130 ? 256 : 128}.png`
