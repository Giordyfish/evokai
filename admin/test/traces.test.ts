import type { TraceSpan } from '@client/core/types'
import { contentText, delta, labelText, layout, outcomeLabel, outcomeTone, paramsOf, queryOf } from '../src/traces'
import { setLocale } from '../src/i18n'

const span = (id: string, start: number, ms: number, parent?: string, kind: TraceSpan['kind'] = 'model'): TraceSpan => ({ id, parent, kind, name: id, start, ms, status: 'ok' })

afterEach(() => setLocale('en'))

test('the waterfall puts children under their parent, in time order, with bars inside the trace', () => {
  const spans = [span('root', 0, 1000, undefined, 'turn'), span('m2', 600, 300, 'root'), span('m1', 10, 500, 'root'), span('g', 20, 0, 'm1', 'guard'), span('orphan', 900, 50, 'gone')]
  const placed = layout(spans)
  expect(placed.map((p) => [p.span.id, p.depth])).toEqual([
    ['root', 0],
    ['m1', 1],
    ['g', 2],
    ['m2', 1],
    ['orphan', 0],
  ])
  const m2 = placed.find((p) => p.span.id === 'm2')!
  expect(m2.left).toBeCloseTo(60)
  expect(m2.width).toBeCloseTo(30)
  expect(placed.find((p) => p.span.id === 'g')!.width).toBeGreaterThan(0)
  expect(placed.every((p) => p.left + p.width <= 100.0001)).toBe(true)
  expect(layout([])).toEqual([])
})

test('a span that is its own parent does not loop', () => {
  expect(layout([{ ...span('a', 0, 10), parent: 'a' }])).toHaveLength(1)
})

test('outcomes have a tone and read in both languages', () => {
  expect(outcomeTone({ status: 'failed', reason: 'model', error: true })).toBe('bad')
  expect(outcomeTone({ status: 'blocked', reason: 'guard', error: false })).toBe('warn')
  expect(outcomeTone({ status: 'done', reason: 'confirm', error: false })).toBe('vi')
  expect(outcomeTone({ status: 'done', reason: 'end_turn', error: false })).toBe('ok')
  expect(outcomeLabel({ status: 'done', reason: 'end_turn' })).toBe('Answered')
  expect(outcomeLabel({ status: 'done', reason: 'something_new' })).toBe('something_new')
  setLocale('it')
  expect(outcomeLabel({ status: 'done', reason: 'confirm' })).toBe('Attende un sì')
  expect(labelText('wrong_tool')).toBe('Strumento sbagliato')
  expect(labelText('custom')).toBe('custom')
})

test('filters travel in the address and back', () => {
  const q = new URLSearchParams('agent=helper&guard=1&errors=0&minMs=10000&feedback=down&view=versions')
  const query = queryOf(q)
  expect(query).toEqual({ agent: 'helper', guard: true, minMs: 10000, feedback: 'down' })
  expect(paramsOf(query).toString()).toBe('agent=helper&feedback=down&guard=1&minMs=10000')
  expect(paramsOf({ guard: false, minMs: 0, agent: '' }).toString()).toBe('')
})

test('version deltas know which way is better', () => {
  expect(delta(1000, 800, false)).toEqual({ diff: -200, better: true })
  expect(delta(80, 60, true)).toEqual({ diff: -20, better: false })
  expect(delta(null, 3, true)).toBeNull()
})

test('content reads as pretty JSON when it is a document', () => {
  expect(contentText('{"a":1}')).toBe('{\n  "a": 1\n}')
  expect(contentText('plain text')).toBe('plain text')
  expect(contentText('{not json}')).toBe('{not json}')
  expect(contentText({ text: 'x' })).toContain('"text": "x"')
})
