import type { GrantView } from '@client/core/types'
import { clientGranted, grantedModels, modelLevel, normalizeModel, toolGranted } from '../src/grants'

const item = (kind: string, name: string, value: unknown, extra: object = {}) => ({ kind, name, value, default: true, override: value, ...extra })

const view = {
  groups: [
    {
      id: 'models',
      items: [
        item('model', 'bedrock:eu.anthropic.claude-haiku-4-5', 'any', { allowed: true, pattern: false, override: null }),
        item('model', 'bedrock:eu.anthropic.claude-sonnet-4-6', 'any', { allowed: true, pattern: false, override: null }),
        item('model', 'bedrock:eu.amazon.nova-2-lite-v1:0', 'fallback', { allowed: true, pattern: false }),
        item('model', 'bedrock:eu.anthropic.claude-sonnet-*', 'off', { allowed: false, pattern: true }),
      ],
    },
    { id: 'mcp', items: [item('mcp', 'bookshop', true, { override: null, children: [item('tool', 'bookshop/order_refund', false, { label: 'order_refund' })] })] },
    { id: 'client', items: [item('client', 'memory', false)] },
  ],
} as unknown as GrantView

test('models: exact overrides, then the longest pattern, else any', () => {
  expect(normalizeModel('eu.amazon.nova-2-lite-v1:0')).toBe('bedrock:eu.amazon.nova-2-lite-v1:0')
  expect(normalizeModel('function:primary')).toBe('function:primary')
  expect(modelLevel(view, 'eu.amazon.nova-2-lite-v1:0')).toBe('fallback')
  expect(modelLevel(view, 'eu.anthropic.claude-sonnet-4-6')).toBe('off')
  expect(modelLevel(view, 'eu.anthropic.claude-haiku-4-5')).toBe('any')
  expect(grantedModels(view).map((m) => m.level)).toEqual(['any', 'fallback'])
})

test('tools follow their server; unknown items follow the default', () => {
  expect(toolGranted(view, 'bookshop', 'order_refund')).toBe(false)
  expect(toolGranted(view, 'bookshop', 'order_get')).toBe(true)
  expect(clientGranted(view, 'memory')).toBe(false)
  expect(clientGranted(view, 'chart')).toBe(true)
})
