/**
 * The conversation's state in one word, for what is slotted next to the chat (a mascot, an indicator): `data-state`
 * on the host and a `statechange` event. It is generic: it says what the conversation is doing, not how to show it.
 *
 * - `idle`: nothing going on;
 * - `listening`: voice mode listens, or the person dictates;
 * - `thinking`: the turn runs (the model is working);
 * - `tool`: a tool runs;
 * - `confirm`: a change waits for the person's confirmation (until they answer);
 * - `speaking`: the answer is being read aloud;
 * - `done`: the turn ended (until the next one starts).
 */
import type { SessionEvent, TurnPhase } from '@evokai/evokai-client/browser'

export type ChatState = 'idle' | 'listening' | 'thinking' | 'tool' | 'confirm' | 'speaking' | 'done'

/** What the controller says now. */
export interface Snapshot {
  phase: TurnPhase
  busy: boolean
  voiceOn: boolean
  dictating: boolean
}

/** The state after a session event. */
export function fromEvent(prev: ChatState, e: SessionEvent): ChatState {
  switch (e.type) {
    case 'tool':
      return e.status === 'start' ? 'tool' : 'thinking'
    case 'confirm':
      return 'confirm'
    case 'done':
      return e.reason === 'confirm' || prev === 'confirm' ? 'confirm' : 'done'
    case 'error':
      return 'idle'
    case 'phase':
      if (e.phase === 'listening') return 'listening'
      if (e.phase === 'speaking') return 'speaking'
      if (e.phase === 'thinking') return prev === 'tool' ? 'tool' : 'thinking'
      return prev === 'confirm' || prev === 'done' ? prev : 'idle'
    default:
      return prev
  }
}

/** The state from the controller's snapshot (dictation and voice listening win; a running turn keeps its detail). */
export function chatState(prev: ChatState, s: Snapshot): ChatState {
  if (s.dictating || (s.voiceOn && s.phase === 'listening')) return 'listening'
  if (s.phase === 'speaking') return 'speaking'
  if (s.busy) return prev === 'tool' || prev === 'confirm' ? prev : 'thinking'
  return prev === 'confirm' || prev === 'done' ? prev : 'idle'
}
