import { moduleOf, moduleOn } from '../src/moduleSwitch'

describe('modules in the console', () => {
  test('both are on when the service does not say', () => {
    expect(moduleOn({}, 'applications')).toBe(true)
    expect(moduleOn({}, 'gateway')).toBe(true)
  })

  test('a module off is off', () => {
    expect(moduleOn({ modules: ['gateway'] }, 'applications')).toBe(false)
    expect(moduleOn({ modules: ['gateway'] }, 'gateway')).toBe(true)
  })

  test('each view belongs to its module or to the core', () => {
    expect(moduleOf({ view: 'gateways' })).toBe('gateway')
    for (const view of ['app', 'apps', 'setup', 'playground', 'usage', 'assistant'] as const) expect(moduleOf({ view })).toBe('applications')
    for (const view of ['health', 'access', 'policy', 'modules', 'preferences'] as const) expect(moduleOf({ view })).toBeNull()
  })
})
