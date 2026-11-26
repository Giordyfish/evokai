/** Policies in the console: the routes each policy calls, refused saves, differences and new tests. */
vi.stubGlobal('location', { origin: 'https://evokai.example', pathname: '/agent/admin/' })

const calls: Array<{ url: string; method: string; headers: Record<string, string>; body: unknown }> = []
let answer: { status: number; body: unknown } = { status: 200, body: {} }
vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
  calls.push({ url, method: init.method ?? 'GET', headers: init.headers as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : undefined })
  return new Response(JSON.stringify(answer.body), { status: answer.status })
})

const policy = await import('../src/policy')
const { EvokaiError } = await import('../src/api')

beforeEach(() => {
  calls.length = 0
  answer = { status: 200, body: {} }
})

describe('the routes', () => {
  it('of an application go under /v1/admin/policy with the console headers', async () => {
    const api = policy.appPolicy('app-1')
    await api.versions()
    await api.diff(1, 3)
    await api.restore(2, { force: true })
    await api.copy({ application: 'crm' }, { note: 'same' })
    await api.deleteTest('t/1')
    await api.run()
    expect(calls.map((c) => `${c.method} ${c.url.replace('https://evokai.example/agent', '')}`)).toEqual([
      'GET /v1/admin/policy/versions',
      'GET /v1/admin/policy/diff?from=1&to=3',
      'POST /v1/admin/policy/versions/2/restore',
      'POST /v1/admin/policy/copy',
      'DELETE /v1/admin/policy/tests/t%2F1',
      'POST /v1/admin/policy/tests/run',
    ])
    expect(calls[2]!.body).toEqual({ force: true })
    expect(calls[3]!.body).toEqual({ application: 'crm', note: 'same' })
    expect(calls[0]!.headers['X-Evokai-Console']).toBe('1')
  })

  it('of the system policy go under /v1/console/system-policy, for no application', async () => {
    const api = policy.systemPolicy()
    await api.diff(2)
    await api.addTest({ name: 'n', input: 'x', outcome: 'blocked' })
    expect(calls.map((c) => c.url.replace('https://evokai.example/agent', ''))).toEqual([
      '/v1/console/system-policy/diff?from=2',
      '/v1/console/system-policy/tests',
    ])
    expect(calls[0]!.headers['X-Evokai-Application']).toBeUndefined()
  })
})

describe('a save refused by policy tests', () => {
  it('gives its failures; any other error gives none', async () => {
    const failure = {
      id: 't1',
      name: 'keys',
      passed: false,
      expected: { outcome: 'blocked', controls: null },
      got: { outcome: 'flagged', controls: { 'input.secrets': 'flagged' } },
    }
    answer = { status: 409, body: { error: 'policy_tests_failed', message: 'x', failures: [failure] } }
    const err = await policy.systemPolicy().restore(1, {}).catch((e: unknown) => e)
    expect(policy.testFailures(err)).toEqual([failure])
    expect(policy.testFailures(new EvokaiError(409, 'conflict', 'x'))).toBeNull()
    expect(policy.testFailures(new Error('x'))).toBeNull()
  })
})

describe('texts', () => {
  it('show a path, a value and what a test expects', () => {
    expect(policy.pathText(['security', 'modes', 'input.injection'])).toBe('security › modes › input.injection')
    expect(policy.valueText(null)).toBe('-')
    expect(policy.valueText('enforce')).toBe('enforce')
    expect(policy.valueText(['eu'])).toBe('["eu"]')
    expect(policy.expectText({ outcome: 'blocked', controls: { scope: 'flagged' } })).toBe('blocked, scope: flagged')
  })

  it('make a new test without the controls left at any', () => {
    expect(policy.newTest(' keys ', 'AKIA', 'any', { 'input.secrets': 'blocked', scope: 'any' })).toEqual({
      name: 'keys',
      input: 'AKIA',
      outcome: null,
      controls: { 'input.secrets': 'blocked' },
    })
    expect(policy.newTest('x', 'y', 'allowed', {})).toEqual({ name: 'x', input: 'y', outcome: 'allowed', controls: null })
  })
})

export {}
