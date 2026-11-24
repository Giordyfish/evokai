import { mascotFor, MASCOT_CLIPS, stillFor } from '../src/mascot/state'
import { Listener, NOD_S, nodDip } from '../src/mascot/listener'

describe('the mascot follows the chat', () => {
  it('maps every chat state to its animation', () => {
    expect(mascotFor('idle', 'idle')).toEqual({ kind: 'clip', name: 'idle-combo', loop: true })
    expect(mascotFor('thinking', 'idle')).toEqual({ kind: 'clip', name: 'idle-ember', loop: true })
    expect(mascotFor('tool', 'thinking')).toEqual({ kind: 'clip', name: 'spell-rune-circle', loop: true })
    expect(mascotFor('confirm', 'tool')).toEqual({ kind: 'clip', name: 'spell-dark-flame', loop: true })
    expect(mascotFor('listening', 'idle')).toEqual({ kind: 'live', mode: 'listen' })
    expect(mascotFor('speaking', 'thinking')).toEqual({ kind: 'live', mode: 'speak' })
  })

  it('celebrates a change applied, only after a confirmation', () => {
    expect(mascotFor('done', 'confirm')).toEqual({ kind: 'clip', name: 'spell-staff-sparkles', loop: false })
    expect(mascotFor('thinking', 'confirm', { applied: true })).toEqual({ kind: 'clip', name: 'spell-staff-sparkles', loop: false })
    expect(mascotFor('done', 'speaking')).toEqual({ kind: 'clip', name: 'idle-combo', loop: true })
  })

  it('greets when the panel opens', () => {
    expect(mascotFor('idle', 'idle', { greeting: true })).toEqual({ kind: 'clip', name: 'spell-hop', loop: false })
  })

  it('every clip it uses ships in two sizes, and a still frame stands in for reduced motion', () => {
    for (const name of MASCOT_CLIPS) {
      expect(name).toMatch(/^(idle|spell)-/)
    }
    expect(stillFor(256)).toBe('fx/still-256.png')
    expect(stillFor(100)).toBe('fx/still-128.png')
  })
})

describe('the nodding listening style', () => {
  it('syllables and pauses come from the level; a pause ends a phrase once', () => {
    const events: string[] = []
    const l = new Listener({ onSyllable: () => events.push('syl'), onPause: () => events.push('pause') })
    let t = 0
    const feed = (level: number, seconds: number) => {
      for (let i = 0; i < seconds / 0.02; i++) l.step(level, (t += 0.02))
    }
    feed(0, 0.2)
    feed(0.6, 0.06)
    feed(0.31, 0.1)
    feed(0.7, 0.06)
    feed(0, 0.6)
    expect(events.filter((e) => e === 'syl').length).toBeGreaterThanOrEqual(2)
    expect(events.at(-1)).toBe('pause')
    expect(events.filter((e) => e === 'pause')).toHaveLength(1)
  })

  it('a nod dips once and comes back', () => {
    expect(nodDip(-1)).toBe(0)
    expect(nodDip(0)).toBe(0)
    expect(nodDip(NOD_S / 2)).toBeCloseTo(1, 5)
    expect(nodDip(NOD_S)).toBe(0)
  })
})
