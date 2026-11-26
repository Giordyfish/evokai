import type { StoredMessage } from '@client/core/types'
import { shownParts } from '../src/shown'

describe('a stored message under review', () => {
  test('a message with ordered parts shows them in their order', () => {
    const events = [
      { type: 'text', delta: 'Let me check. ' },
      { type: 'tool', name: 'orders_month', status: 'done' },
      { type: 'text', delta: 'Done.' },
    ] as StoredMessage['events']
    expect(shownParts({ text: 'Let me check. Done.', events }).map((e) => e.type)).toEqual(['text', 'tool', 'text'])
  })

  test('an older message shows its text first, then its events', () => {
    const events = [{ type: 'navigate', path: '/x' }] as StoredMessage['events']
    expect(shownParts({ text: 'Old.', events })).toEqual([{ type: 'text', delta: 'Old.' }, ...events])
    expect(shownParts({ text: '', events })).toEqual(events)
  })
})
