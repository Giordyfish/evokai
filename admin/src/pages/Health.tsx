/** Health: the database, the models, the MCP servers, voice, the purge job and the build. */
import { useState } from 'preact/hooks'

import type { ConsoleHealth } from '@client/core/types'
import { describe, root } from '../api'
import { ago, ms, modelName, pl } from '../format'
import { HealthOrb, Skeleton, toast } from '../magic'
import { setTitle, useLoad } from '../state'
import { Btn, Hero, Panel, StatusTag, Tag } from '../ui'
import { Loads } from './common'
import { t, tn } from '../i18n'

interface Check {
  id: string
  name: string
  status: 'ok' | 'slow' | 'down' | null
  detail: string
  metric: string
  history?: boolean[]
}

function checks(h: ConsoleHealth): Check[] {
  const out: Check[] = [
    {
      id: 'db',
      name: t('health.database'),
      status: h.database.status,
      detail: `${h.database.dialect}${h.database.schema ? `, ${t('health.schema', { schema: h.database.schema })}` : ''}, ${
        h.database.revision == null
          ? t('health.migNone')
          : h.database.revision === h.database.head
            ? t('health.migHead')
            : t('health.migBehind', { revision: h.database.revision, head: h.database.head })
      }${h.database.error ? `, ${h.database.error}` : ''}`,
      metric: ms(h.database.latencyMs),
    },
  ]
  const models = h.models.items
  const failing = models.filter((m) => m.lastErrorAt && (!m.lastOkAt || m.lastErrorAt > m.lastOkAt))
  out.push({
    id: 'models',
    name: t('health.models', { region: h.models.region }),
    status: !models.length ? null : failing.length === models.length ? 'down' : failing.length ? 'slow' : 'ok',
    detail: models.length
      ? `${t('health.answering', { models: models.filter((m) => !failing.includes(m)).map((m) => modelName(m.model)).join(', ') || t('fmt.none') })}${failing.length ? `; ${failing.map((m) => t('health.failing', { model: modelName(m.model), error: m.lastError })).join(', ')}` : ''}`
      : t('health.noModelCalled'),
    metric: models.length ? pl(models.reduce((s, m) => s + m.calls, 0), 'n.call') : t('health.idle'),
  })
  for (const m of h.mcp)
    out.push({
      id: `mcp-${m.application}-${m.name}`,
      name: t('health.mcp', { name: m.name }),
      status: m.status,
      detail: `${m.applicationName}, ${m.tools != null ? pl(m.tools, 'n.tool') : t('health.toolsNotListed')}, ${m.auth}${m.error ? `; ${m.error}` : ''}${m.checkedAt ? `; ${t('health.checked', { when: ago(m.checkedAt) })}` : ''}`,
      metric: m.latencyMs != null ? ms(m.latencyMs) : t('health.notChecked'),
    })
  out.push({
    id: 'voice',
    name: t('health.voice'),
    status: h.voice.enabled ? 'ok' : null,
    detail: h.voice.enabled
      ? `Pipecat, Silero VAD${h.voice.smartTurn ? ', Smart Turn' : ''}, STT ${h.voice.stt}, TTS ${h.voice.tts}`
      : t('health.voiceOff'),
    metric: h.voice.enabled ? pl(h.voice.connections, 'n.connection') : t('health.off'),
  })
  if (h.sharedState)
    out.push({
      id: 'shared',
      name: t('health.shared'),
      status: h.sharedState.kind === 'memory' ? 'ok' : h.sharedState.ok ? 'ok' : 'down',
      detail:
        h.sharedState.kind === 'memory'
          ? t('health.sharedMemory')
          : h.sharedState.ok
            ? t('health.sharedRedis')
            : t('health.sharedDown', { error: h.sharedState.error ?? '' }),
      metric: h.otel ? t('health.otelOn') : t('health.otelOff'),
    })
  out.push({
    id: 'purge',
    name: t('health.purge'),
    status: h.purge.error ? 'down' : h.purge.lastRunAt ? 'ok' : null,
    detail: h.purge.lastRunAt
      ? `${t('health.lastRun', { when: ago(h.purge.lastRunAt * 1000), deleted: pl(h.purge.deleted ?? 0, 'n.conversation') })}${h.purge.error ? `; ${h.purge.error}` : ''}`
      : t('health.notRun', { apps: pl(h.purge.ttlApplications, 'n.application') }),
    metric: h.purge.nextRunAt ? t('health.next', { when: ago(h.purge.nextRunAt * 1000) }) : t('health.hourly'),
  })
  return out
}

export function Health() {
  setTitle(t('page.health'))
  const [checking, setChecking] = useState(false)
  const load = useLoad(() => root.health(false), [])
  const run = async () => {
    setChecking(true)
    try {
      const h = await root.health(true)
      load.set(h)
      const bad = checks(h).filter((c) => c.status === 'down' || c.status === 'slow').length
      toast(t('health.checksComplete'), bad ? t('health.notHealthy', { checks: pl(bad, 'n.check') }) : t('health.allAnswer'), bad ? null : 'embers')
    } catch (e) {
      toast(t('health.checksFailed'), describe(e), null)
    } finally {
      setChecking(false)
    }
  }
  return (
    <Loads load={load} what={t('what.health')}>
      {(h) => {
        const list = checks(h)
        const bad = list.filter((c) => c.status === 'down' || c.status === 'slow')
        const ok = !bad.length
        return (
          <>
            <Hero
              title={t('page.health')}
              sub={ok ? t('health.allOk') : tn('health.needsLook', bad.length, { names: bad.map((c) => c.name).join(', ') })}
              actions={
                <Btn kind="pri" busy={checking ? t('health.checking') : false} onClick={() => void run()}>
                  {t('health.run')}
                </Btn>
              }
            />
            <div class="hstat">
              <HealthOrb ok={ok} />
              <span>
                {ok ? (
                  <>
                    <b>{t('health.allHealthy', { n: list.length })}</b>
                    {t('health.orIdle')}
                  </>
                ) : (
                  <>
                    <b>{t('health.badLead', { checks: pl(bad.length, 'n.check') })}</b>
                    {t('health.badRest', { n: list.length - bad.length })}
                  </>
                )}
              </span>
            </div>
            <div class="grid g3">
              {list.map((c) => (
                <Panel key={c.id}>
                  <div class="hcard">
                    <div class="top">
                      <b>{c.name}</b>
                      {c.status ? <StatusTag status={c.status} /> : <Tag>{t('health.idleTag')}</Tag>}
                    </div>
                    {checking ? <Skeleton rows={2} /> : <p>{c.detail}</p>}
                    {c.id === 'models' && h.models.items.length ? (
                      <div class="up" title={t('health.lastCalls')}>
                        {h.models.items
                          .flatMap((m) => Array.from({ length: Math.min(m.calls, 30) }, (_, i) => i < m.failures))
                          .slice(0, 30)
                          .map((failed, i) => (
                            <i key={i} class={failed ? 'w' : ''} />
                          ))}
                      </div>
                    ) : null}
                    <div class="m">
                      <span>{c.id === 'models' ? h.models.allow.join(', ') : ''}</span>
                      <span>{c.metric}</span>
                    </div>
                  </div>
                </Panel>
              ))}
              <Panel>
                <div class="hcard">
                  <div class="top">
                    <b>{t('health.build')}</b>
                  </div>
                  <p>
                    evokai {h.build.version}
                    {h.build.commit ? `, ${t('health.commit', { commit: h.build.commit })}` : ''}, Python {h.build.python}, {h.models.region}
                  </p>
                </div>
              </Panel>
            </div>
          </>
        )
      }}
    </Loads>
  )
}
