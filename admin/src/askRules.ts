/**
 * Ask rules in the console: rows as the editor holds them, the rules as the service stores them, and the checks the
 * service makes again on save (see evokai.guard.ask). A rule only adds a confirmation.
 */
import type { AskRule } from '@client/core/types'

/** One rule as the editor holds it: every field a string, tags comma separated. */
export interface RuleRow {
  id: string
  action: string
  argument: string
  part: 'value' | 'host'
  resource: string
  effect: 'ask' | 'default'
  labelEn: string
  labelIt: string
  tags: string
}

export const blankRow = (): RuleRow => ({
  id: '',
  action: '',
  argument: '',
  part: 'value',
  resource: '*',
  effect: 'ask',
  labelEn: '',
  labelIt: '',
  tags: '',
})

/** A stored rule as an editor row. */
export function toRow(r: AskRule): RuleRow {
  return {
    id: r.id ?? '',
    action: r.action,
    argument: r.argument ?? '',
    part: r.part === 'host' ? 'host' : 'value',
    resource: r.resource ?? '*',
    effect: r.effect === 'default' ? 'default' : 'ask',
    labelEn: r.label?.en ?? '',
    labelIt: r.label?.it ?? '',
    tags: (r.tags ?? []).join(', '),
  }
}

/** An editor row as the rule the service stores (empty fields left out). */
export function toRule(row: RuleRow): AskRule {
  const label: Record<string, string> = {}
  if (row.labelEn.trim()) label.en = row.labelEn.trim()
  if (row.labelIt.trim()) label.it = row.labelIt.trim()
  const tags = row.tags
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
  const argument = row.argument.trim()
  const out: AskRule = { action: row.action.trim(), effect: row.effect }
  if (row.id.trim()) out.id = row.id.trim()
  if (argument) {
    out.argument = argument
    if (row.part === 'host') out.part = 'host'
  }
  const resource = row.resource.trim() || '*'
  if (argument && resource !== '*') out.resource = resource
  if (Object.keys(label).length) out.label = label
  if (tags.length) out.tags = tags
  return out
}

/** The rows that can be saved (a rule needs its tool). */
export const rulesOf = (rows: RuleRow[]): AskRule[] => rows.filter((r) => r.action.trim()).map(toRule)

/** Whether two whole-value patterns (`*`, `?`) can match the same name (as evokai.guard.ask.overlap). */
export function overlap(a: string, b: string): boolean {
  const memo = new Map<string, boolean>()
  const ov = (i: number, j: number): boolean => {
    const k = `${i},${j}`
    const known = memo.get(k)
    if (known !== undefined) return known
    let r: boolean
    if (i === a.length && j === b.length) r = true
    else if (a[i] === '*') r = ov(i + 1, j) || (j < b.length && ov(i, j + 1))
    else if (b[j] === '*') r = ov(i, j + 1) || (i < a.length && ov(i + 1, j))
    else r = i < a.length && j < b.length && (a[i] === b[j] || a[i] === '?' || b[j] === '?') && ov(i + 1, j + 1)
    memo.set(k, r)
    return r
  }
  return ov(0, 0)
}

export type RuleProblem = { n: number; kind: 'lifts' | 'orphan' | 'argument'; tool: string }

/**
 * What the service would refuse: a `default` rule that names a tool that changes data exactly (its confirmation can
 * never be removed), a `default` rule with no `ask` above it in the same list that could match the same tools (it
 * could only lift another level's ask, which a rule never does), and a value pattern without its argument.
 *
 * Returns problems as `{ n, kind, tool }`, `n` counted from 1.
 */
export function ruleProblems(rows: RuleRow[], mutating: ReadonlySet<string>): RuleProblem[] {
  const out: RuleProblem[] = []
  rows.forEach((r, i) => {
    const action = r.action.trim()
    if (!action) return
    if (r.effect === 'default') {
      if (!/[*?]/.test(action) && mutating.has(action)) out.push({ n: i + 1, kind: 'lifts', tool: action })
      else if (!rows.slice(0, i).some((x) => x.effect === 'ask' && x.action.trim() && overlap(x.action.trim(), action)))
        out.push({ n: i + 1, kind: 'orphan', tool: action })
    }
    if (!r.argument.trim() && r.resource.trim() && r.resource.trim() !== '*') out.push({ n: i + 1, kind: 'argument', tool: action })
  })
  return out
}

/** Move a row up (-1) or down (+1); the order matters (the last rule that matches wins). */
export function moveRow(rows: RuleRow[], i: number, d: -1 | 1): RuleRow[] {
  const j = i + d
  if (j < 0 || j >= rows.length) return rows
  const next = [...rows]
  ;[next[i], next[j]] = [next[j]!, next[i]!]
  return next
}
