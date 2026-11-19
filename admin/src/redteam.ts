/**
 * The red team as the console reads it: category and grader labels, the order failures are shown in, the score and
 * its tone against the gate, the trend chart's geometry, the comparison with the previous version and the gate's
 * warning. Pure functions over the service's views, so they are tested without a page.
 */
import type { RedTeamResult, RedTeamRunDetail, RedTeamRuns, RedTeamTally } from '@client/core/types'

import { has, intlTag, num, t, tn, type TextKey } from './i18n'

/** The built-in categories, in the order the console lists them. */
export const CATEGORIES = [
  'direct_injection',
  'indirect_injection',
  'prompt_extraction',
  'exfiltration',
  'excessive_agency',
  'pii_leakage',
  'jailbreak',
  'obfuscation',
] as const
export const SEVERITIES = ['high', 'medium', 'low'] as const
export const GATE_MODES = ['off', 'monitor', 'enforce'] as const
export type GateMode = (typeof GATE_MODES)[number]
/** The least score a version needs when the application sets none. */
export const DEFAULT_MIN_SCORE = 80

/** A category as people read it; an id the console does not know yet as the service sent it. */
export const categoryLabel = (id: string): string => (has(`rt.cat.${id}`) ? t(`rt.cat.${id}` as TextKey) : id)
/** What a category tries, in one sentence (empty for an unknown one). */
export const categoryHint = (id: string): string => (has(`rt.catd.${id}`) ? t(`rt.catd.${id}` as TextKey) : '')
/** A grader as people read it. */
export const graderLabel = (type: string): string => (has(`rt.gr.${type}`) ? t(`rt.gr.${type}` as TextKey) : type)
/** A severity as the security page says it. */
export const severityLabel = (s: string): string => ((SEVERITIES as readonly string[]).includes(s) ? t(`sec.s.${s}` as TextKey) : s)

/** A language code as a name in the console's language (`es` reads `Spanish`, `spagnolo`), else the code. */
export function languageName(code: string, tag: string = intlTag()): string {
  try {
    const name = new Intl.DisplayNames([tag], { type: 'language' }).of(code)
    return name && name !== code ? name[0]!.toUpperCase() + name.slice(1) : code
  } catch {
    return code
  }
}

/** A score out of 100 with at most one decimal (`86.5`, `86,5`), or a dash. */
export const scoreText = (v: number | null | undefined): string => (v == null ? '-' : num(Math.round(v * 10) / 10, { maximumFractionDigits: 1 }))

/** A change of score in points, signed: `+4.5 pts`, `-2 pts`, `0 pts`. */
export function pointsText(v: number | null | undefined): string {
  if (v == null) return '-'
  const r = Math.round(v * 10) / 10
  return t('evals.pts', { v: `${r > 0 ? '+' : ''}${num(r, { maximumFractionDigits: 1 })}` })
}

/** The size of a change in points, without its sign (the words say the direction): `4.5 pts`. */
const sizeText = (v: number): string => t('evals.pts', { v: num(Math.round(Math.abs(v) * 10) / 10, { maximumFractionDigits: 1 }) })

/** How a score reads against the gate's minimum: at or above it, close below it (10 points), or well below. */
export function scoreTone(v: number | null | undefined, min: number): '' | 'ok' | 'warn' | 'bad' {
  if (v == null) return ''
  if (v >= min) return 'ok'
  return v >= min - 10 ? 'warn' : 'bad'
}

const severityRank = (s: string): number => {
  const i = (SEVERITIES as readonly string[]).indexOf(s)
  return i < 0 ? SEVERITIES.length : i
}
const categoryRank = (c: string): number => {
  const i = (CATEGORIES as readonly string[]).indexOf(c)
  return i < 0 ? CATEGORIES.length : i
}

/** A run's results split by outcome: failures first by severity (high first) then category, then the rest in suite order. */
export function byOutcome(results: RedTeamResult[]): { failed: RedTeamResult[]; skipped: RedTeamResult[]; passed: RedTeamResult[] } {
  const failed = results
    .map((r, i) => [r, i] as const)
    .filter(([r]) => r.outcome === 'failed')
    .sort(([a, i], [b, j]) => severityRank(a.severity) - severityRank(b.severity) || categoryRank(a.category) - categoryRank(b.category) || i - j)
    .map(([r]) => r)
  return {
    failed,
    skipped: results.filter((r) => r.outcome === 'skipped'),
    passed: results.filter((r) => r.outcome === 'passed'),
  }
}

/** The per-category tallies in the console's order (unknown categories last, by id), with the share that held. */
export function categoryRows(byCategory: Record<string, RedTeamTally> | undefined): Array<{ id: string; tally: RedTeamTally; heldPct: number | null }> {
  const entries = Object.entries(byCategory ?? {})
  entries.sort(([a], [b]) => categoryRank(a) - categoryRank(b) || a.localeCompare(b))
  return entries.map(([id, tally]) => {
    const graded = tally.passed + tally.failed
    return { id, tally, heldPct: graded ? Math.round((tally.passed / graded) * 1000) / 10 : null }
  })
}

/** The comparison with the previous version in words: a heading (score change) and the attacks fixed and newly failing. */
export function comparisonText(c: RedTeamRunDetail['comparison']): { tone: '' | 'ok' | 'bad'; head: string; fixed: string | null; failing: string | null } | null {
  if (!c) return null
  const version = c.version.slice(0, 7)
  const d = c.scoreDelta
  const head =
    d == null
      ? t('rt.cmp.noScore', { version })
      : d > 0
        ? t('rt.cmp.up', { version, pts: sizeText(d) })
        : d < 0
          ? t('rt.cmp.down', { version, pts: sizeText(d) })
          : t('rt.cmp.same', { version })
  const tone = c.newlyFailing.length ? 'bad' : d != null && d > 0 ? 'ok' : ''
  return {
    tone,
    head,
    fixed: c.fixed.length ? tn('rt.cmp.fixed', c.fixed.length) : null,
    failing: c.newlyFailing.length ? tn('rt.cmp.failing', c.newlyFailing.length) : null,
  }
}

/** The warning the gate shows while the active version's latest score is below its minimum (gate `monitor` or `enforce`). */
export function gateWarning(runs: Pick<RedTeamRuns, 'gate' | 'versions'>): { version: string; score: number; min: number; mode: 'monitor' | 'enforce' } | null {
  const { mode, minScore } = runs.gate
  if (mode === 'off') return null
  const active = runs.versions.find((v) => v.active)
  if (!active || active.score == null || active.score >= minScore) return null
  return { version: active.version, score: active.score, min: minScore, mode }
}

/** Whether publishing a version must override the gate (it holds back any version with a publish state under `enforce`). */
export const needsForce = (state: 'pending' | 'blocked' | null | undefined, mode: GateMode): boolean => mode === 'enforce' && !!state

/**
 * The trend chart's geometry: one point per finished run (oldest first) on a score axis that starts below the lowest
 * score and the minimum (in steps of 10), so a few points of change stay visible.
 */
export function trendGeometry(
  trend: RedTeamRuns['trend'],
  min: number,
  size: { w: number; h: number; left: number; right: number; top: number; bottom: number },
): { lo: number; points: Array<{ x: number; y: number; version: string; score: number; at: string }>; line: string; area: string; minY: number; y: (v: number) => number } {
  const scores = trend.map((p) => p.score)
  const lo = Math.max(0, Math.min(Math.floor((Math.min(min, ...scores) - 5) / 10) * 10, 90))
  const { w, h, left, right, top, bottom } = size
  const y = (v: number) => top + ((100 - Math.max(lo, Math.min(100, v))) / (100 - lo)) * (h - top - bottom)
  const n = trend.length
  const x = (i: number) => (n < 2 ? left + (w - left - right) / 2 : left + (i * (w - left - right)) / (n - 1))
  const points = trend.map((p, i) => ({ x: x(i), y: y(p.score), ...p }))
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
  const base = (h - bottom).toFixed(1)
  const area = points.length > 1 ? `${line} L${points[points.length - 1]!.x.toFixed(1)} ${base} L${points[0]!.x.toFixed(1)} ${base}Z` : ''
  return { lo, points, line, area, minY: y(min), y }
}
