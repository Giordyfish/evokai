/**
 * Template syntax in the console. Agent instructions, prompts and confirm labels are Handlebars; a version saved before
 * Handlebars is `legacy`: it keeps working as it is, and the console marks it (`evokai.agents.templates`).
 */

type Texts = { template_syntax?: string | null; instructions?: string | null; prompt?: unknown; confirm_labels?: unknown }

/** Whether a stored agent definition is legacy (a definition without the field was saved before Handlebars). */
export const isLegacy = (d: { template_syntax?: string | null } | null | undefined): boolean =>
  !!d && (d.template_syntax ?? 'legacy') === 'legacy'

/** Whether a prompt version is legacy. */
export const isLegacyPrompt = (v: { templateSyntax?: string | null } | null | undefined): boolean =>
  !!v && (v.templateSyntax ?? 'legacy') === 'legacy'

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/**
 * The definition to save. A legacy agent whose instructions, prompt reference and labels are unchanged stays legacy
 * (another model, other settings: the service accepts it); anything else is saved in Handlebars.
 */
export function withSyntax<T extends Texts>(draft: T, base: T, isNew: boolean): T {
  const keep =
    !isNew &&
    isLegacy(base) &&
    same(draft.instructions, base.instructions) &&
    same(draft.prompt, base.prompt) &&
    same(draft.confirm_labels, base.confirm_labels)
  return { ...draft, template_syntax: keep ? 'legacy' : 'handlebars' }
}
