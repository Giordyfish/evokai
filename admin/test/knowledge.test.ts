import type { GrantView, KnowledgeBase, KnowledgeList } from '@client/core/types'
import { cited, where } from '../src/cite'
import { knowledgeGranted } from '../src/grants'
import { bodyOf, formOf } from '../src/knowledge'

const item = (kind: string, name: string, value: unknown, extra: object = {}) => ({ kind, name, value, default: true, override: null, ...extra })

test('citation markers in order of first use; links in parentheses are not markers', () => {
  expect(cited('A [2] then [1], again [2] and [x](/y) or [10].')).toEqual([2, 1, 10])
  expect(cited('[3](/link) only')).toEqual([])
  expect(where({ location: { page: 3, section: 'Art. 2', charStart: 0, charEnd: 1 } })).toBe('p. 3 · Art. 2')
  expect(where({ location: { page: 3, pageEnd: 4, charStart: 0, charEnd: 1 } })).toBe('p. 3-4')
  expect(where({ location: { charStart: 0, charEnd: 1 } })).toBe('')
})

test('knowledge grants: the capability first, then each base', () => {
  const view = (all: boolean, norme: boolean) =>
    ({ groups: [{ id: 'knowledge', items: [item('knowledge', '*', all, { children: [item('knowledge', 'norme', norme)] })] }] }) as unknown as GrantView
  expect(knowledgeGranted(view(true, true), 'norme')).toBe(true)
  expect(knowledgeGranted(view(true, false), 'norme')).toBe(false)
  expect(knowledgeGranted(view(true, false), 'altro')).toBe(true)
  expect(knowledgeGranted(view(false, true), 'norme')).toBe(false)
  expect(knowledgeGranted({ groups: [] } as unknown as GrantView)).toBe(true)
})

test('the settings form reads a base and writes the body the service takes', () => {
  const list = { embeddingModels: ['bedrock:amazon.titan-embed-text-v2:0', 'test:hash'], defaultModel: 'bedrock:amazon.titan-embed-text-v2:0' } as KnowledgeList
  const blank = formOf(null, list)
  expect(blank.embeddingModel).toBe('bedrock:amazon.titan-embed-text-v2:0')
  expect(blank.dimensions).toBe(1024)
  const base = {
    name: 'norme',
    title: 'Norme',
    description: 'Delibere',
    embeddingModel: 'test:hash',
    dimensions: 512,
    language: 'italian',
    chunking: { max_chars: 1200, overlap_chars: 150, table_max_chars: 3000 },
    retrieval: { top_k: 4, min_score: 0.2, hybrid: true, mmr: true, mmr_lambda: 0.6, candidates: 40 },
    access: ['finance', 'staff'],
    linkTemplate: null,
    sources: [{ kind: 'url', url: 'https://www.example.org/returns-policy.pdf' }, { kind: 'connector', server: 'drive' }],
    retentionDays: 30,
  } as unknown as KnowledgeBase
  const f = formOf(base, list)
  expect(f.access).toBe('finance, staff')
  expect(f.urls).toBe('https://www.example.org/returns-policy.pdf')
  const body = bodyOf({ ...f, access: 'finance, , ops', retention: '' }, base)
  expect(body.access).toEqual(['finance', 'ops'])
  expect(body.retentionDays).toBeNull()
  expect(body.retrieval).toMatchObject({ top_k: 4, min_score: 0.2, mmr: true, mmr_lambda: 0.6 })
  expect(body.chunking).toEqual({ max_chars: 1200, overlap_chars: 150, table_max_chars: 3000 })
  expect(body.sources).toEqual([
    { kind: 'url', url: 'https://www.example.org/returns-policy.pdf' },
    { kind: 'connector', server: 'drive' },
  ])
})
