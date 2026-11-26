/** What a stored message showed the person, for the console's conversation review. */
import type { EvokaiEvent, StoredMessage } from '@client/core/types'

/**
 * The events a stored message showed, in the order it showed them. Since ordered parts its events hold its text too; a
 * message stored before has its text apart, shown first.
 */
export function shownParts(m: Pick<StoredMessage, 'text' | 'events'>): EvokaiEvent[] {
  const events: EvokaiEvent[] = Array.isArray(m.events) ? m.events : []
  if (events.some((e) => e.type === 'text') || !m.text) return events
  return [{ type: 'text', delta: m.text } as EvokaiEvent, ...events]
}
