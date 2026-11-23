/** Reading a grant view: what an application may use, as the server resolves it (see evokai.grants). */
import type { GrantItem, GrantView, ModelGrant } from '@client/core/types'

const PROVIDERS = new Set([
  'bedrock', 'openai', 'openai-chat', 'openai-responses', 'azure', 'anthropic', 'mistral', 'google-gla', 'google-vertex',
  'groq', 'cohere', 'ollama', 'deepseek', 'openrouter', 'huggingface', 'test', 'function',
])

/** A model name with its provider, as the server stores grants: bare Bedrock ids get `bedrock:`. */
export function normalizeModel(m: string): string {
  const i = m.indexOf(':')
  return i > 0 && PROVIDERS.has(m.slice(0, i)) ? m : `bedrock:${m}`
}

const glob = (p: string): RegExp =>
  new RegExp(`^${p.replace(/[.+^${}()|\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`)

export const group = (view: GrantView, id: string): GrantItem[] => view.groups.find((g) => g.id === id)?.items ?? []

/** How a model may be used: an exact override wins, else the longest matching pattern, else `any`. */
export function modelLevel(view: GrantView, model: string): ModelGrant {
  const name = normalizeModel(model)
  const items = group(view, 'models')
  const exact = items.find((x) => !x.pattern && x.name === name)
  if (exact && exact.override != null) return exact.override as ModelGrant
  const pats = items.filter((x) => x.pattern && x.override != null).sort((a, b) => b.name.length - a.name.length)
  for (const p of pats) if (glob(p.name).test(name)) return p.override as ModelGrant
  return 'any'
}

/** Whether the deployment and the application's allow-list let a model be used at all. */
export function modelAllowed(view: GrantView, model: string): boolean {
  const item = group(view, 'models').find((x) => x.name === normalizeModel(model))
  return item?.allowed ?? true
}

/** The models an agent may pick: concrete, allowed and not off. */
export function grantedModels(view: GrantView): Array<{ name: string; level: ModelGrant }> {
  return group(view, 'models')
    .filter((x) => !x.pattern && x.allowed)
    .map((x) => ({ name: x.name, level: modelLevel(view, x.name) }))
    .filter((x) => x.level !== 'off')
}

const flag = (item: GrantItem | undefined, fallback = true): boolean => (item ? item.value !== false : fallback)

export const serverGranted = (view: GrantView, server: string): boolean =>
  flag(group(view, 'mcp').find((x) => x.name === server))

export function toolGranted(view: GrantView, server: string, tool: string): boolean {
  const s = group(view, 'mcp').find((x) => x.name === server)
  if (!flag(s)) return false
  return flag(s?.children?.find((t) => t.label === tool || t.name === `${server}/${tool}`))
}

export const skillGranted = (view: GrantView, name: string): boolean => flag(group(view, 'skills').find((x) => x.name === name))
export const clientGranted = (view: GrantView, name: string): boolean => flag(group(view, 'client').find((x) => x.name === name))
/** Whether knowledge search is granted, and the named base with it. */
export function knowledgeGranted(view: GrantView, base?: string): boolean {
  const all = group(view, 'knowledge').find((x) => x.name === '*')
  if (!flag(all)) return false
  return base == null || flag(all?.children?.find((x) => x.name === base))
}
/** Code execution: granted to the application, and a sandbox the deployment turned on. */
export function codeGranted(view: GrantView): { granted: boolean; enabled: boolean } {
  const item = group(view, 'code').find((x) => x.name === '*')
  return { granted: flag(item), enabled: item?.enabled === true }
}
/** A provider built-in tool (`builtin:web_search`...). */
export const builtinGranted = (view: GrantView, kind: string): boolean => flag(group(view, 'builtin').find((x) => x.name === kind))
/** Files people attach to chat messages (`attachments:*`). */
export const attachmentsGranted = (view: GrantView): boolean => flag(group(view, 'attachments').find((x) => x.name === '*'))
/** Confirmations over the OpenAI-compatible API (`openai:confirm`). */
export const openaiConfirmGranted = (view: GrantView): boolean => flag(group(view, 'openai').find((x) => x.name === 'confirm'))
/** Capabilities of the catalog an agent may turn on: granted, offered by the deployment, installed and
 * never on the server itself. */
export function capabilityItems(view: GrantView): GrantItem[] {
  return group(view, 'capabilities')
}
export const capabilityUsable = (x: GrantItem): boolean =>
  x.value === true && x.offered === true && x.installed === true && x.host !== 'local'
export const channelGranted = (view: GrantView, name: string): boolean => flag(group(view, 'channels').find((x) => x.name === name))
