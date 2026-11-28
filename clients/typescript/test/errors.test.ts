import { EvokaiNotFoundError, errorFrom } from '../src/core/http'

describe('errors', () => {
  test('the flat shape gives its code and message', () => {
    const e = errorFrom(409, { error: 'slug_taken', message: 'Taken.' })
    expect([e.code, e.message]).toEqual(['slug_taken', 'Taken.'])
  })

  test('a module that is off answers a nested error, read the same way', () => {
    const body = { error: { code: 'module_disabled', message: 'The gateway module is off on this deployment.', module: 'gateway' } }
    const e = errorFrom(404, body)
    expect(e).toBeInstanceOf(EvokaiNotFoundError)
    expect([e.code, e.message]).toEqual(['module_disabled', 'The gateway module is off on this deployment.'])
  })

  test('without a body the status names the error', () => {
    expect(errorFrom(404, undefined).code).toBe('not_found')
  })
})
