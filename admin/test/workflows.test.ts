import { arrowsOf, canCancel, canRetry, ordered, problemLines, runHref, stepDetail, stepIds, TEMPLATE, toYaml, viewOf } from '../src/workflows'

describe('workflow routes', () => {
  it('read the list, a new workflow, a run and a workflow to edit from the id', () => {
    expect(viewOf(null)).toEqual({ kind: 'list' })
    expect(viewOf('new')).toEqual({ kind: 'new' })
    expect(viewOf('run:5f1c')).toEqual({ kind: 'run', id: '5f1c' })
    expect(viewOf('mail-triage')).toEqual({ kind: 'edit', name: 'mail-triage' })
    expect(runHref('bookshop', 'r1')).toBe('#/apps/bookshop/workflows/run%3Ar1')
  })
})

describe('the flow', () => {
  it('shows a condition as two ways, the missing one going to the step below', () => {
    const cond = { id: 'route', kind: 'condition' as const, title: '', if: 'steps.x.output.ok', then: 'order' }
    expect(arrowsOf(cond, 'technical')).toEqual([
      { label: 'then', to: 'order' },
      { label: 'else', to: 'technical' },
    ])
    expect(arrowsOf({ ...cond, then: undefined }, null)).toEqual([
      { label: 'then', to: 'end' },
      { label: 'else', to: 'end' },
    ])
    expect(arrowsOf({ id: 'a', kind: 'agent', title: '', next: 'end' }, 'b')).toEqual([{ label: 'next', to: 'end' }])
    expect(arrowsOf({ id: 'a', kind: 'agent', title: '' }, 'b')).toEqual([])
  })

  it('lists nested steps and says what each one does', () => {
    const steps = [
      { id: 'fan', kind: 'parallel' as const, title: '', branches: [{ name: 'a', steps: [{ id: 'w', kind: 'wait' as const, title: '', seconds: 60 }] }] },
      { id: 'send', kind: 'tool' as const, title: '', tool: 'back/reply' },
    ]
    expect(stepIds(steps)).toEqual(['fan', 'w', 'send'])
    expect(stepDetail(steps[0]!.branches![0]!.steps[0]!)).toBe('60s')
    expect(stepDetail(steps[1]!)).toBe('back/reply')
  })
})

describe('the editor', () => {
  it('groups problems by line, those without one under 0', () => {
    const m = problemLines([
      { path: 'steps[0].agent', message: 'unknown agent x', line: 5 },
      { path: 'steps[0].input', message: 'bad', line: 5 },
      { path: '', message: 'a workflow is an object', line: null },
    ])
    expect(m.get(5)?.length).toBe(2)
    expect(m.get(0)?.[0]?.message).toBe('a workflow is an object')
  })

  it('writes a definition as YAML, quoting what YAML would misread', () => {
    const yaml = toYaml({
      name: 'returns',
      steps: [
        { id: 'ask', kind: 'approval', question: 'Send {{ input.period }}?', approvers: { roles: ['operator'] } },
        { id: 'w', kind: 'wait', seconds: 60, until: null },
      ],
      triggers: [],
      flag: 'yes',
      ver: '0017',
      text: 'two\nlines',
    })
    expect(yaml).toBe(
      [
        'name: returns',
        'steps:',
        '  - id: ask',
        '    kind: approval',
        '    question: "Send {{ input.period }}?"',
        '    approvers:',
        '      roles: [operator]',
        '  - id: w',
        '    kind: wait',
        '    seconds: 60',
        '    until: null',
        'triggers: []',
        'flag: "yes"',
        'ver: "0017"',
        'text: "two\\nlines"',
        '',
      ].join('\n'),
    )
  })

  it('puts a definition in reading order', () => {
    expect(Object.keys(ordered({ steps: [], output: 1, name: 'x', zeta: true, triggers: [] }))).toEqual(['name', 'triggers', 'steps', 'output', 'zeta'])
  })

  it('starts a new workflow from a template with every common kind of step', () => {
    expect(TEMPLATE).toContain('kind: agent')
    expect(TEMPLATE).toContain('kind: approval')
    expect(canRetry('failed', 'tool_error')).toBe(true)
    expect(canRetry('failed', 'invalid_input')).toBe(false)
    expect(canCancel('waiting')).toBe(true)
    expect(canCancel('done')).toBe(false)
  })
})
