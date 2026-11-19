/** "What it can use": the grants of an application in eight cells. */
import type { GrantSummary } from '@client/core/types'
import { modelName, pl } from '../format'
import { t } from '../i18n'

export const storageLabel = (s: GrantSummary): string =>
  t(s.storage.length === 2 ? 'storage.both' : s.storage[0] === 'evokai' ? 'storage.evokai' : s.storage[0] === 'external' ? 'storage.external' : 'storage.none')

export const voiceText = (s: GrantSummary): string =>
  t(s.voice.browser && s.voice.server ? 'voice.both' : s.voice.browser ? 'voice.browser' : s.voice.server ? 'voice.server' : 'voice.none')

const of = (x: { on: number; total: number }): string => t('sum.of', { on: x.on, total: x.total })

export function summaryRows(s: GrantSummary, overrides: number): Array<[string, string, string]> {
  const fb = s.models.fallbackOnly.map(modelName)
  return [
    [t('sum.models'), of(s.models), fb.length ? t('sum.fallbackOnly', { models: fb.join(', ') }) : t('sum.allPrimary')],
    [t('sum.mcpTools'), of(s.tools), pl(s.mcpServers.on, 'n.server')],
    [t('sum.skills'), of(s.skills), ''],
    [t('sum.clientTools'), of(s.clientTools), s.clientTools.off.length ? t('sum.notClient', { tools: s.clientTools.off.join(', ') }) : t('sum.allTools')],
    [t('sum.voice'), voiceText(s), pl(s.voice.voices, 'n.voice')],
    [t('sum.storage'), storageLabel(s), ''],
    [t('sum.serverRuns'), t(s.serverRuns ? 'sum.allowed' : 'sum.notAllowed'), s.browserSessions ? '' : t('sum.browserOff')],
    [t('sum.a2a'), t(s.a2a ? 'sum.allowed' : 'sum.notAllowed'), s.a2a ? '' : t('sum.a2aOff')],
    [t('sum.overrides'), String(overrides), t('sum.setHere')],
  ]
}

export function Summary({ s, overrides }: { s: GrantSummary; overrides: number }) {
  return (
    <dl class="sum">
      {summaryRows(s, overrides).map(([k, v, sub]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>
            {v}
            {sub ? <small>{sub}</small> : null}
          </dd>
        </div>
      ))}
    </dl>
  )
}
