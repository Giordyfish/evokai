import type { SessionEvent } from '@evokai/evokai-client/browser'

import { type ChatState, chatState, fromEvent } from '../src/chatstate'

const ev = (e: Record<string, unknown>): SessionEvent => ({ turnId: 't1', ...e }) as unknown as SessionEvent

describe('the chat state on the host', () => {
  test('a turn: thinking, a tool, thinking again, speaking, done', () => {
    let s: ChatState = 'idle'
    s = fromEvent(s, ev({ type: 'phase', phase: 'thinking', previous: 'idle' }))
    expect(s).toBe('thinking')
    s = fromEvent(s, ev({ type: 'tool', name: 'list_agents', status: 'start' }))
    expect(s).toBe('tool')
    s = fromEvent(s, ev({ type: 'tool', name: 'list_agents', status: 'done' }))
    expect(s).toBe('thinking')
    s = fromEvent(s, ev({ type: 'phase', phase: 'speaking', previous: 'thinking' }))
    expect(s).toBe('speaking')
    s = fromEvent(s, ev({ type: 'done', reason: 'end_turn', conversationId: 'c' }))
    expect(s).toBe('done')
    s = fromEvent(s, ev({ type: 'phase', phase: 'idle', previous: 'speaking' }))
    expect(s).toBe('done')
  })

  test('a confirmation is kept until the person answers', () => {
    let s: ChatState = 'tool'
    s = fromEvent(s, ev({ type: 'confirm', actionId: 'a', items: ['Delete the agent support'] }))
    expect(s).toBe('confirm')
    s = fromEvent(s, ev({ type: 'done', reason: 'confirm', conversationId: 'c' }))
    expect(s).toBe('confirm')
    s = fromEvent(s, ev({ type: 'phase', phase: 'idle', previous: 'thinking' }))
    expect(s).toBe('confirm')
    s = fromEvent(s, ev({ type: 'phase', phase: 'thinking', previous: 'idle' }))
    expect(s).toBe('thinking')
  })

  test('listening comes from voice and dictation', () => {
    expect(chatState('idle', { phase: 'listening', busy: false, voiceOn: true, dictating: false })).toBe('listening')
    expect(chatState('idle', { phase: 'idle', busy: false, voiceOn: false, dictating: true })).toBe('listening')
    expect(chatState('listening', { phase: 'idle', busy: false, voiceOn: false, dictating: false })).toBe('idle')
    expect(chatState('done', { phase: 'idle', busy: false, voiceOn: false, dictating: false })).toBe('done')
    expect(chatState('tool', { phase: 'thinking', busy: true, voiceOn: false, dictating: false })).toBe('tool')
  })

  test('an error ends in idle', () => {
    expect(fromEvent('tool', ev({ type: 'error', code: 'model', message: 'x' }))).toBe('idle')
  })
})
