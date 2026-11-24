/**
 * Choosing the built-in assistant's model: the badges, the requirements shown in the disclaimer, and the rule that a
 * model that does not meet them needs a second disclaimer, ticked, before it can be activated.
 */
import type { Fit } from '../fit'
import { t } from '../i18n'
import type { Tone } from '../ui'

export type Status = 'recommended' | 'not_recommended' | 'not_measured'

export interface Measured {
  toolCallsPct: number | null
  confirmPct: number | null
  redteamPct: number | null
  contextTokens: number | null
  measuredAt: string | null
  evokaiVersion: string | null
  source: 'try' | 'release' | null
}

export interface Candidate {
  model: string
  label: string
  status: Status
  recommended: boolean
  measured: Measured | null
  /** The suitability for the assistant: an estimate from published facts, or the measurement. */
  estimate?: Fit
}

export interface Requirements {
  toolCallsPct: number
  confirmPct: number
  redteamPct: number
  contextTokens: number
  streaming: boolean
  tools: boolean
}

export interface Trial {
  runId: string
  model: string | null
  status: string
  progressPct: number
  budgetPct: number
  measured: Measured | null
  startedAt: string
}

export interface AssistantState {
  available: boolean
  active: boolean
  /** Switched off by an owner: no launcher, no application in the list; the model is kept. */
  off?: boolean
  model: string | null
  applicationId?: string
  slug?: string
  locked?: boolean
  updateAvailable?: boolean
  requirements: Requirements
  candidates: Candidate[]
  trial: Trial | null
}

/** The badge of a model. */
export function badge(status: Status): { text: string; tone: Tone } {
  if (status === 'recommended') return { text: t('assistant.badgeRecommended'), tone: 'ok' }
  if (status === 'not_recommended') return { text: t('assistant.badgeNotRecommended'), tone: 'warn' }
  return { text: t('assistant.badgeNotMeasured'), tone: '' }
}

/** The requirements as the disclaimer lists them, in the current language. */
export function requirementLines(r: Requirements): string[] {
  return [
    t('assistant.reqTools', { pct: r.toolCallsPct }),
    t('assistant.reqConfirm', { pct: r.confirmPct }),
    t('assistant.reqRedteam', { pct: r.redteamPct }),
    t('assistant.reqContext', { tokens: Math.round(r.contextTokens / 1000) }),
    t('assistant.reqStreaming'),
  ]
}

/** Whether choosing this model opens the second disclaimer. */
export const needsSecondDisclaimer = (c: Candidate): boolean => !c.recommended

/** Whether the model can be activated now (a not recommended one only once the second disclaimer is ticked). */
export const canActivate = (c: Candidate, ticked: boolean): boolean => c.recommended || ticked

/** The body of the activation request. */
export function activateBody(c: Candidate, ticked: boolean): { model: string; acceptNotRecommended?: boolean } {
  return c.recommended ? { model: c.model } : { model: c.model, acceptNotRecommended: ticked }
}

/** Whether the assistant is there for this person: available in the deployment and activated (unknown: no). */
export const assistantActive = (s: AssistantState | null): boolean => !!(s?.available && s.active)

/** Whether a try is still going. */
export const trialRunning = (s: AssistantState | null): boolean => s?.trial?.status === 'running'
