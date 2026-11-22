/** The data table of a module: keys are checked against the enum of the parameter its template looks up. */
import { dataCheck, lookupParams } from '../src/modules'

const schema = {
  type: 'object',
  properties: { section: { type: 'string', enum: ['poetry', 'crime'] }, name: { type: 'string', maxLength: 20 } },
}

describe('the data table check', () => {
  it('finds the parameters a template looks up', () => {
    expect(lookupParams('{{lookup data section}} and {{#if (lookup data name)}}x{{/if}}')).toEqual(['section', 'name'])
    expect(lookupParams('Hello {{name}}')).toEqual([])
  })

  it('flags a key outside the parameter enum before saving, and the values without an entry', () => {
    const r = dataCheck('{{lookup data section}}', schema, { poetry: 'Poems.', drama: 'Plays.' })
    expect(r.unknown).toEqual(['drama'])
    expect(r.missing).toEqual(['crime'])
    expect(r.allowed).toEqual(['poetry', 'crime'])
  })

  it('has nothing to check without a lookup on a choice parameter', () => {
    expect(dataCheck('Hello {{name}}', schema, { a: 'x' })).toEqual({ allowed: null, unknown: [], missing: [] })
  })
})
