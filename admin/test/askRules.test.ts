import { blankRow, moveRow, overlap, ruleProblems, rulesOf, toRow, toRule } from '../src/askRules'

describe('ask rules in the console', () => {
  it('turn rows into the rules the service stores, leaving empty fields out', () => {
    const row = { ...blankRow(), action: ' crm_get ', argument: 'url', part: 'host' as const, resource: '*.internal', labelIt: 'Interni', tags: 'team:ops, , sales' }
    expect(toRule(row)).toEqual({ action: 'crm_get', effect: 'ask', argument: 'url', part: 'host', resource: '*.internal', label: { it: 'Interni' }, tags: ['team:ops', 'sales'] })
    expect(toRule({ ...blankRow(), action: 'x', effect: 'default' })).toEqual({ action: 'x', effect: 'default' })
    expect(rulesOf([blankRow(), { ...blankRow(), action: 'y' }])).toEqual([{ action: 'y', effect: 'ask' }])
  })

  it('read stored rules back unchanged', () => {
    const rule = { id: 'vip', action: 'crm_*', argument: 'code', resource: 'VIP-*', effect: 'ask' as const, label: { en: 'Key accounts', it: 'Clienti chiave' }, tags: ['team:ops'] }
    expect(toRule(toRow(rule))).toEqual(rule)
  })

  it('flag what the service refuses: lifting a write or another level, a value without its argument', () => {
    const rows = [
      { ...blankRow(), action: 'crm_*', effect: 'default' as const },
      { ...blankRow(), action: 'order_*' },
      { ...blankRow(), action: 'order_refund', effect: 'default' as const },
      { ...blankRow(), action: 'order_?et', effect: 'default' as const },
      { ...blankRow(), action: 'crm_get', resource: 'VIP-*' },
    ]
    expect(ruleProblems(rows, new Set(['order_refund']))).toEqual([
      { n: 1, kind: 'orphan', tool: 'crm_*' },
      { n: 3, kind: 'lifts', tool: 'order_refund' },
      { n: 5, kind: 'argument', tool: 'crm_get' },
    ])
    expect([overlap('crm_*', '*_get'), overlap('order_?et', 'crm_*'), overlap('a?c', '*b*')]).toEqual([true, false, true])
  })

  it('keep their order, which decides (the last match wins)', () => {
    const rows = ['a', 'b', 'c'].map((action) => ({ ...blankRow(), action }))
    expect(moveRow(rows, 2, -1).map((r) => r.action)).toEqual(['a', 'c', 'b'])
    expect(moveRow(rows, 0, -1)).toBe(rows)
  })
})
