/**
 * The reset of the assistant's system application: its preview (`GET /v1/admin/application/reset/preview`), the JSON
 * of the items it deletes for the owner to keep, and the typed slug it needs.
 */
import { has, t } from '../i18n'

export interface ResetItem {
  kind: string
  name: string
  action: 'delete' | 'restore'
  item?: Record<string, unknown>
}

export interface ResetPreview {
  slug: string
  items: ResetItem[]
  /** The plan's fingerprint: the reset takes it back and deletes nothing the preview did not show. */
  plan: string
}

/** Whether a reset was refused because what it deletes changed since its preview. */
export const planChanged = (e: unknown): boolean => (e as { code?: string } | null)?.code === 'plan_changed'

/** The items the reset deletes. */
export const deleted = (p: ResetPreview): ResetItem[] => p.items.filter((i) => i.action === 'delete')

/** The settings the reset sets back, with their values now (credentials masked). */
export const settingsBefore = (p: ResetPreview): Record<string, unknown> =>
  p.items.find((i) => i.kind === 'settings' && i.name === 'application')?.item ?? {}

/** The file an owner downloads before the reset: every deleted item with its definition and the settings it sets back (no secrets). */
export function resetExport(p: ResetPreview, now = new Date()): { filename: string; text: string } {
  const day = now.toISOString().slice(0, 10)
  return {
    filename: `${p.slug}-reset-${day}.json`,
    text: JSON.stringify({ application: p.slug, exportedAt: now.toISOString(), deleted: deleted(p), settings: settingsBefore(p) }, null, 2),
  }
}

/** A reset or an unlock goes ahead only once the person typed the application's slug. */
export const slugTyped = (typed: string, slug: string): boolean => typed.trim() === slug

/** A kind as the preview names it. */
export function kindText(kind: string): string {
  const key = `reset.kind.${kind}`
  return has(key) ? t(key) : kind
}
