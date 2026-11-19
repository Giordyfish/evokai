/** The sections of an application and the counts the navigation shows. */
import type { ConsoleApplication } from '@client/core/types'
import { t } from './i18n'
import type { Tab } from './state'

/** The sections in their order; the label is read in the current language with `tabLabel`. */
export const TAB_ORDER: Tab[] = [
  'overview',
  'agents',
  'evals',
  'perms',
  'mcp',
  'connections',
  'channels',
  'skills',
  'knowledge',
  'workflows',
  'approvals',
  'keys',
  'limits',
  'usage',
  'traces',
  'conversations',
  'security',
  'privacy',
  'providers',
  'compliance',
  'audit',
]

export const tabLabel = (tab: Tab): string => t(`tab.${tab}`)

/** The sections with their labels, in the current language. */
export const tabs = (): Array<[Tab, string]> => TAB_ORDER.map((k) => [k, tabLabel(k)])

export function voiceLabel(a: ConsoleApplication): string {
  const v = a.grants.voice
  return t(v.browser && v.server ? 'voice.both' : v.browser ? 'voice.browser' : v.server ? 'voice.server' : 'voice.none')
}

export function tabCounts(a: ConsoleApplication): Partial<Record<Tab, number>> {
  return {
    agents: a.counts.agents,
    mcp: a.counts.mcpServers,
    skills: a.counts.skills,
    keys: a.counts.keys,
    perms: a.counts.overrides,
    workflows: a.counts.workflows,
    approvals: a.counts.approvals || undefined,
  }
}
