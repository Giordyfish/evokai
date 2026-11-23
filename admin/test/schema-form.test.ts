/** The params form of a prompt module: each allowed property type gets its control, anything else none. */
import { controlFor, unsupportedParams } from '../src/modules'

describe('controlFor', () => {
  it('maps each allowed property type to its control', () => {
    expect(controlFor({ type: 'string', enum: ['brief', 'detailed'] })).toBe('select')
    expect(controlFor({ type: 'boolean' })).toBe('switch')
    expect(controlFor({ type: 'string', maxLength: 40 })).toBe('text')
    expect(controlFor({ type: 'integer', minimum: 1 })).toBe('number')
    expect(controlFor({ type: 'number' })).toBe('number')
    expect(controlFor({ type: 'array', items: { type: 'string', enum: ['poetry', 'crime'] }, maxItems: 2 })).toBe('multi')
  })

  it('renders no control for a type outside the subset', () => {
    expect(controlFor({ type: 'object', properties: {} })).toBeNull()
    expect(controlFor({ type: 'array', items: { type: 'object' } })).toBeNull()
    expect(controlFor({ $ref: '#/x' })).toBeNull()
    expect(controlFor(null)).toBeNull()
  })

  it('lists the parameters it cannot render, so the page shows the API error instead', () => {
    const schema = {
      type: 'object',
      properties: { style: { type: 'string', enum: ['a'] }, who: { type: 'object' } },
    }
    expect(unsupportedParams(schema)).toEqual(['who'])
    expect(unsupportedParams(null)).toEqual([])
  })
})
