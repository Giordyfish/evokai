/** The visual parameter builder writes exactly the restricted JSON Schema, and reads it back. */
import { blankParam, builderToSchema, schemaToBuilder, type BuilderParam } from '../src/modules'

describe('the parameter builder', () => {
  it('writes a choice parameter as exactly a string enum with its default', () => {
    const p: BuilderParam = { ...blankParam('choice'), name: 'style', values: ['brief', 'detailed', 'listed'], default: 'detailed' }
    const schema = builderToSchema([p])
    expect(schema).toEqual({ type: 'object', properties: { style: { type: 'string', enum: ['brief', 'detailed', 'listed'], default: 'detailed' } } })
    expect(schemaToBuilder(schema)).toEqual([p])
  })

  it('writes every kind and reads it back the same', () => {
    const params: BuilderParam[] = [
      { ...blankParam('yesno'), name: 'beginner', default: 'true', description: 'New to the shop' },
      { ...blankParam('number'), name: 'shelf', min: '1', max: '9' },
      { ...blankParam('text'), name: 'name', maxLength: 40, required: true },
      { ...blankParam('multi'), name: 'genres', values: ['poetry', 'crime'], maxItems: 2 },
    ]
    const schema = builderToSchema(params)
    expect(schema.properties).toEqual({
      beginner: { type: 'boolean', default: true, description: 'New to the shop' },
      shelf: { type: 'number', minimum: 1, maximum: 9 },
      name: { type: 'string', maxLength: 40 },
      genres: { type: 'array', items: { type: 'string', enum: ['poetry', 'crime'] }, maxItems: 2 },
    })
    expect(schema.required).toEqual(['name'])
    expect(schemaToBuilder(schema)).toEqual(params)
  })

  it('gives up on a schema it cannot show (the raw JSON view stays)', () => {
    expect(schemaToBuilder({ type: 'object', properties: { x: { type: 'object' } } })).toBeNull()
    expect(schemaToBuilder(null)).toEqual([])
  })
})
