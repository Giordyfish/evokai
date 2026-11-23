/**
 * The agent editor's custom client tools: rows as typed (the parameters as JSON text), checked before saving with the
 * same rules as the service (`docs/client-tools.md`); the service checks the schema in full on save.
 */
import type { TextKey } from './i18n'

export interface ClientToolRow {
  name: string
  description: string
  /** The JSON Schema of the arguments, as typed. */
  parameters: string
  wait: boolean
}

export interface ClientTool {
  name: string
  description: string
  parameters: Record<string, unknown>
  wait: boolean
}

const NAME = /^[a-z][a-z0-9_]{1,47}$/
const RESERVED = new Set(['navigate', 'chart', 'diagram', 'ask', 'confirm', 'memory', 'session'])
export const MAX_TOOLS = 32
const MAX_SCHEMA_BYTES = 16_384
const MAX_DESCRIPTION = 1000

export const blankTool = (): ClientToolRow => ({
  name: '',
  description: '',
  parameters: '{\n  "type": "object",\n  "properties": {}\n}',
  wait: false,
})

export const toRow = (t: ClientTool): ClientToolRow => ({
  name: t.name,
  description: t.description,
  parameters: JSON.stringify(t.parameters ?? { type: 'object', properties: {} }, null, 2),
  wait: !!t.wait,
})

/** A row as the service takes it (call only on a row without a problem). */
export const fromRow = (r: ClientToolRow): ClientTool => ({
  name: r.name.trim(),
  description: r.description.trim(),
  parameters: JSON.parse(r.parameters) as Record<string, unknown>,
  wait: r.wait,
})

function schemaProblem(v: unknown): boolean {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return true
  const s = v as Record<string, unknown>
  // Tools take an object of arguments.
  if (s.type !== 'object') return true
  if (s.properties !== undefined && (typeof s.properties !== 'object' || s.properties === null || Array.isArray(s.properties))) return true
  if (s.required !== undefined && (!Array.isArray(s.required) || s.required.some((x) => typeof x !== 'string'))) return true
  return false
}

/** What is wrong with one row (the i18n key of the message), or null. `others` are the names of the other rows. */
export function toolProblem(r: ClientToolRow, others: string[]): TextKey | null {
  const name = r.name.trim()
  if (!NAME.test(name)) return 'ag.customName'
  if (RESERVED.has(name)) return 'ag.customReserved'
  if (others.includes(name)) return 'ag.customTwice'
  const description = r.description.trim()
  if (!description || description.length > MAX_DESCRIPTION) return 'ag.customDescription'
  let parsed: unknown
  try {
    parsed = JSON.parse(r.parameters)
  } catch {
    return 'ag.customJson'
  }
  if (JSON.stringify(parsed).length > MAX_SCHEMA_BYTES) return 'ag.customSize'
  if (schemaProblem(parsed)) return 'ag.customSchema'
  return null
}

/** The problem of each row, in order (null where the row is fine). */
export const toolProblems = (rows: ClientToolRow[]): Array<TextKey | null> =>
  rows.map((r, i) => toolProblem(r, rows.filter((_, j) => j !== i).map((x) => x.name.trim())))
