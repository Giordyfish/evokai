/**
 * A model's suitability for an agent (`GET /v1/admin/agents/{agent}/suitability`, and each candidate of the
 * assistant): an estimate from sourced facts, or a measurement when there is one. The badge always says which.
 */
import { day } from './format'
import { has, num, t } from './i18n'
import type { Tone } from './ui'

export interface Fit {
  verdict: 'recommended' | 'not_recommended' | 'to_verify'
  reasons: string[]
  basis: 'estimate' | 'measured'
  facts: {
    contextTokens?: number | null
    needContext?: number
    tau2Pass1?: number
    tau2AsOf?: string
    tau2Source?: string
    needTau2?: number
  }
}

/** The badge: never a measurement when it is an estimate, and "to verify" when facts are missing. */
export function fitBadge(f: Fit): { text: string; tone: Tone } {
  if (f.verdict === 'to_verify') return { text: t('fit.toVerify'), tone: '' }
  const measured = f.basis === 'measured'
  if (f.verdict === 'recommended') return { text: t(measured ? 'fit.recommendedMeasured' : 'fit.recommendedEstimate'), tone: 'ok' }
  return { text: t(measured ? 'fit.notRecommendedMeasured' : 'fit.notRecommendedEstimate'), tone: 'warn' }
}

const k = (tokens: number | null | undefined): string => num(Math.round((tokens ?? 0) / 1000))

/** The reasons, in the current language. */
export function fitReasons(f: Fit): string[] {
  const vars = {
    have: k(f.facts.contextTokens),
    need: k(f.facts.needContext),
    score: num(f.facts.tau2Pass1 ?? 0),
    bar: num(f.facts.needTau2 ?? 0),
    date: f.facts.tau2AsOf ? day(f.facts.tau2AsOf, true) : '',
  }
  return f.reasons.map((code) => {
    const key = `fit.reason.${code}`
    return has(key) ? t(key, vars) : code
  })
}
