/** The security policy as the console edits it: modes chosen per control, the rest as JSON settings. */
import type { SecurityMode, SecurityPolicy } from '@client/core/types'

export const MODES: readonly SecurityMode[] = ['off', 'monitor', 'enforce'] as const
/** No choice here: the scope above (or the defaults) decides. */
export const INHERIT = 'default'
export type Choice = SecurityMode | typeof INHERIT

/** The policy without the modes (the settings edited as JSON). */
export function settingsOf(policy: SecurityPolicy): Record<string, unknown> {
  const { mode: _m, modes: _ms, ...rest } = policy as Record<string, unknown>
  return rest
}

/** The policy to save: the chosen modes plus the settings, empty parts left out. */
export function policyFrom(base: Choice, modes: Record<string, Choice>, settings: Record<string, unknown>): SecurityPolicy {
  const set = Object.fromEntries(Object.entries(modes).filter(([, v]) => v !== INHERIT)) as Record<string, SecurityMode>
  const out: Record<string, unknown> = { ...settings }
  if (base !== INHERIT) out.mode = base
  if (Object.keys(set).length) out.modes = set
  return out as SecurityPolicy
}

/** What `injection.classifier` may say (`true` and `false` are still read as `default` and `none`). */
export const CLASSIFIER_CHOICES = ['none', 'default', 'bedrock_guardrails', 'prompt_guard', 'azure_prompt_shields', 'model_armor', 'mistral_moderation', 'custom'] as const
export type ClassifierChoice = (typeof CLASSIFIER_CHOICES)[number]
/** No classifier chosen at this scope: the one above (or the defaults, none) decides. Not `default`, which is a choice. */
export const UNSET = 'inherit'

/** The classifier part of the settings JSON, or null when the JSON is not valid. */
export function classifierIn(json: string): { choice: ClassifierChoice | typeof UNSET; maxChars: number | null } | null {
  let parsed: unknown
  try {
    parsed = json.trim() ? JSON.parse(json) : {}
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const inj = (parsed as { injection?: { classifier?: unknown; classifier_max_chars?: unknown } }).injection ?? {}
  const raw = inj.classifier === true ? 'default' : inj.classifier === false ? 'none' : inj.classifier
  const choice = (CLASSIFIER_CHOICES as readonly unknown[]).includes(raw) ? (raw as ClassifierChoice) : UNSET
  const maxChars = typeof inj.classifier_max_chars === 'number' ? inj.classifier_max_chars : null
  return { choice, maxChars }
}

/** The settings JSON with the classifier choice (or its per-turn characters) changed; null when the JSON is not valid. */
export function withClassifier(json: string, patch: { choice?: ClassifierChoice | typeof UNSET; maxChars?: number | null }): string | null {
  if (classifierIn(json) === null) return null
  const settings = (json.trim() ? JSON.parse(json) : {}) as Record<string, unknown>
  const inj = { ...((settings.injection as Record<string, unknown> | undefined) ?? {}) }
  if (patch.choice !== undefined) {
    if (patch.choice === UNSET) delete inj.classifier
    else inj.classifier = patch.choice
  }
  if (patch.maxChars !== undefined) {
    if (patch.maxChars === null) delete inj.classifier_max_chars
    else inj.classifier_max_chars = patch.maxChars
  }
  const out = { ...settings }
  if (Object.keys(inj).length) out.injection = inj
  else delete out.injection
  return JSON.stringify(out, null, 2)
}
