/**
 * Prompt modules in the console: the controls of a params form, the visual parameter builder (it writes the restricted
 * JSON Schema the service accepts, `evokai.agents.modules`) and the check of a module's data table.
 */

type Json = Record<string, unknown>

/** The control that edits one parameter: a choice, a yes/no, a short text, a number or a multi-choice. */
export type Control = 'select' | 'switch' | 'text' | 'number' | 'multi'

const isObj = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v)
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every((x) => typeof x === 'string')

/** The control for a property of a parameter schema, or null for a type outside the subset (not rendered). */
export function controlFor(prop: unknown): Control | null {
  if (!isObj(prop)) return null
  if (prop.type === 'string') return strings(prop.enum) ? 'select' : 'text'
  if (prop.type === 'boolean') return 'switch'
  if (prop.type === 'integer' || prop.type === 'number') return 'number'
  if (prop.type === 'array' && isObj(prop.items) && prop.items.type === 'string' && strings(prop.items.enum)) return 'multi'
  return null
}

/** The properties of a schema, in order. */
export const propsOf = (schema: unknown): Array<[string, Json]> =>
  isObj(schema) && isObj(schema.properties) ? Object.entries(schema.properties).filter((e): e is [string, Json] => isObj(e[1])) : []

/** The parameters a form cannot render (the page shows the API's check instead). */
export const unsupportedParams = (schema: unknown): string[] => propsOf(schema).filter(([, p]) => controlFor(p) === null).map(([n]) => n)

/** The choices of a select or multi-choice property. */
export const choices = (prop: Json): string[] =>
  strings(prop.enum) ? prop.enum : isObj(prop.items) && strings(prop.items.enum) ? prop.items.enum : []

// ── the visual builder ─────────────────────────────────────────────────────────────────────────────────────────────

/** The kinds a person picks from, in the builder. */
export type ParamType = 'choice' | 'yesno' | 'number' | 'text' | 'multi'
export const PARAM_TYPES: ParamType[] = ['choice', 'yesno', 'number', 'text', 'multi']

/** One parameter as the builder edits it (numbers and defaults as typed, converted when the schema is written). */
export interface BuilderParam {
  name: string
  type: ParamType
  /** Choices (choice, multi). */
  values: string[]
  /** The default as typed: a choice, `true`/`false`, a number, a text, or choices separated by commas. */
  default: string
  description: string
  required: boolean
  /** Short text: the most characters (the service needs it, at most 200). */
  maxLength: number
  pattern: string
  /** Number: whole numbers only. */
  integer: boolean
  min: string
  max: string
  /** Multi-choice: the most choices. */
  maxItems: number
}

export function blankParam(type: ParamType): BuilderParam {
  return { name: '', type, values: [], default: '', description: '', required: false, maxLength: 100, pattern: '', integer: false, min: '', max: '', maxItems: 10 }
}

const numberOrUndefined = (s: string): number | undefined => (s.trim() === '' || Number.isNaN(Number(s)) ? undefined : Number(s))

function propOf(p: BuilderParam): Json {
  const out: Json = {}
  const def = p.default.trim()
  switch (p.type) {
    case 'choice':
      Object.assign(out, { type: 'string', enum: p.values })
      if (def) out.default = def
      break
    case 'yesno':
      out.type = 'boolean'
      if (def) out.default = def === 'true'
      break
    case 'number': {
      out.type = p.integer ? 'integer' : 'number'
      const min = numberOrUndefined(p.min)
      const max = numberOrUndefined(p.max)
      if (min !== undefined) out.minimum = min
      if (max !== undefined) out.maximum = max
      const d = numberOrUndefined(def)
      if (d !== undefined) out.default = d
      break
    }
    case 'text':
      Object.assign(out, { type: 'string', maxLength: p.maxLength })
      if (p.pattern) out.pattern = p.pattern
      if (def) out.default = p.default
      break
    case 'multi':
      Object.assign(out, { type: 'array', items: { type: 'string', enum: p.values }, maxItems: p.maxItems })
      if (def)
        out.default = def
          .split(',')
          .map((x) => x.trim())
          .filter(Boolean)
      break
  }
  if (p.description) out.description = p.description
  return out
}

/** The restricted JSON Schema the builder's parameters describe. */
export function builderToSchema(params: BuilderParam[]): { type: 'object'; properties: Json; required?: string[] } {
  const properties: Json = {}
  for (const p of params) properties[p.name] = propOf(p)
  const required = params.filter((p) => p.required).map((p) => p.name)
  return required.length ? { type: 'object', properties, required } : { type: 'object', properties }
}

const KNOWN: Record<ParamType, string[]> = {
  choice: ['type', 'enum', 'default', 'description'],
  yesno: ['type', 'default', 'description'],
  number: ['type', 'minimum', 'maximum', 'default', 'description'],
  text: ['type', 'maxLength', 'pattern', 'default', 'description'],
  multi: ['type', 'items', 'maxItems', 'default', 'description'],
}

function paramOf(name: string, prop: Json, required: boolean): BuilderParam | null {
  const control = controlFor(prop)
  const type: ParamType | null =
    control === 'select' ? 'choice' : control === 'switch' ? 'yesno' : control === 'number' ? 'number' : control === 'text' ? 'text' : control === 'multi' ? 'multi' : null
  if (!type || Object.keys(prop).some((k) => !KNOWN[type].includes(k))) return null
  const p: BuilderParam = { ...blankParam(type), name, required, description: typeof prop.description === 'string' ? prop.description : '' }
  const d = prop.default
  if (type === 'choice') p.values = choices(prop)
  if (type === 'multi') {
    p.values = choices(prop)
    p.maxItems = typeof prop.maxItems === 'number' ? prop.maxItems : p.maxItems
  }
  if (type === 'text') {
    p.maxLength = typeof prop.maxLength === 'number' ? prop.maxLength : p.maxLength
    p.pattern = typeof prop.pattern === 'string' ? prop.pattern : ''
  }
  if (type === 'number') {
    p.integer = prop.type === 'integer'
    p.min = typeof prop.minimum === 'number' ? String(prop.minimum) : ''
    p.max = typeof prop.maximum === 'number' ? String(prop.maximum) : ''
  }
  if (d !== undefined) p.default = Array.isArray(d) ? d.join(', ') : String(d)
  return p
}

/** The builder's parameters for a schema, or null when it holds something the builder cannot show (raw JSON then). */
export function schemaToBuilder(schema: unknown): BuilderParam[] | null {
  if (schema == null) return []
  if (!isObj(schema) || schema.type !== 'object') return null
  const required = strings(schema.required) ? schema.required : []
  const out: BuilderParam[] = []
  for (const [name, prop] of propsOf(schema)) {
    const p = paramOf(name, prop, required.includes(name))
    if (!p) return null
    out.push(p)
  }
  return out
}

// ── the data table ─────────────────────────────────────────────────────────────────────────────────────────────────

/** The parameters a template reads its data table with (`{{lookup data <parameter>}}`, also as a subexpression). */
export function lookupParams(text: string): string[] {
  const found = [...text.matchAll(/[({]\s*lookup\s+data\s+([A-Za-z_][A-Za-z0-9_]*)\s*[)}]/g)].map((m) => m[1]!)
  return [...new Set(found)]
}

/**
 * The data table against the choices of the parameter its template looks it up with: keys that no choice can pick
 * (`unknown`, refused before saving) and choices without an entry (`missing`, they render empty).
 */
export function dataCheck(text: string, schema: unknown, data: Record<string, string>): { allowed: string[] | null; unknown: string[]; missing: string[] } {
  const props = new Map(propsOf(schema))
  const enums = lookupParams(text)
    .map((n) => props.get(n))
    .filter((p): p is Json => !!p && controlFor(p) === 'select')
    .map(choices)
  if (!enums.length) return { allowed: null, unknown: [], missing: [] }
  const allowed = [...new Set(enums.flat())]
  const keys = Object.keys(data)
  return { allowed, unknown: keys.filter((k) => !allowed.includes(k)), missing: allowed.filter((k) => !keys.includes(k)) }
}
