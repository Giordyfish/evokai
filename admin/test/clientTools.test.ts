/** The custom client tools editor checks each row with the service's rules before saving. */
import { blankTool, fromRow, toolProblem, toolProblems, toRow } from '../src/clientTools'

const LIGHT = { name: 'kiosk_light', description: 'Turn the kiosk light on or off.', parameters: '{"type":"object","properties":{"on":{"type":"boolean"}}}', wait: true }

describe('custom client tools', () => {
  it('accepts a valid tool and round-trips it', () => {
    expect(toolProblem(LIGHT, [])).toBeNull()
    const tool = fromRow(LIGHT)
    expect(tool.parameters).toEqual({ type: 'object', properties: { on: { type: 'boolean' } } })
    expect(fromRow(toRow(tool))).toEqual(tool)
  })

  it('refuses bad names, reserved names and a name twice', () => {
    expect(toolProblem({ ...LIGHT, name: 'Kiosk' }, [])).toBe('ag.customName')
    expect(toolProblem({ ...LIGHT, name: 'chart' }, [])).toBe('ag.customReserved')
    expect(toolProblem({ ...LIGHT, name: 'session' }, [])).toBe('ag.customReserved')
    expect(toolProblems([LIGHT, LIGHT])).toEqual(['ag.customTwice', 'ag.customTwice'])
  })

  it('refuses a missing description, broken JSON and a schema that is not one', () => {
    expect(toolProblem({ ...LIGHT, description: ' ' }, [])).toBe('ag.customDescription')
    expect(toolProblem({ ...LIGHT, description: 'd'.repeat(1001) }, [])).toBe('ag.customDescription')
    expect(toolProblem({ ...LIGHT, parameters: '{"type":' }, [])).toBe('ag.customJson')
    expect(toolProblem({ ...LIGHT, parameters: '{"type": 3}' }, [])).toBe('ag.customSchema')
    expect(toolProblem({ ...LIGHT, parameters: '[]' }, [])).toBe('ag.customSchema')
    expect(toolProblem({ ...LIGHT, parameters: '{"type": "string"}' }, [])).toBe('ag.customSchema')
    expect(toolProblem({ ...LIGHT, parameters: JSON.stringify({ type: 'object', description: 'x'.repeat(17_000) }) }, [])).toBe('ag.customSize')
  })

  it('a new row needs a name and a description', () => {
    expect(toolProblem(blankTool(), [])).toBe('ag.customName')
  })
})
