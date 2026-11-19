/**
 * The cheapest model that passes, on an agent's Evals page: its eval cases run on the current model and on each
 * cheaper allowed one; a model passes when its pass rate stays within the tolerance. Each model's cost is a share of
 * the current model's (never an amount), and one click saves a new version on the chosen model.
 */
import { useEffect, useState } from 'preact/hooks'

import type { ConsoleApplication, ModelComparisonRow } from '@client/core/types'
import { describe, EvokaiError, forApp } from '../api'
import { comparisonRows, relativeCostText, wholePct } from '../costs'
import { ago, bar, modelName, ms } from '../format'
import { num, t, tn } from '../i18n'
import { done, sparkle, toast } from '../magic'
import { pointsText } from '../redteam'
import { useLoad } from '../state'
import { Bar, Btn, Panel, Tag } from '../ui'
import { Loads } from './common'
import { statusTag } from './run-status'

const DEFAULT_TOLERANCE = 5

export function ModelChoice(p: { app: ConsoleApplication; agent: string; hasCases: boolean; onSwitched: () => void }) {
  const { app, agent } = p
  const api = forApp(app.id)
  const [tolerance, setTolerance] = useState(String(DEFAULT_TOLERANCE))
  const [starting, setStarting] = useState(false)
  const [switching, setSwitching] = useState<string | null>(null)
  const load = useLoad(() => api.admin.costs.recommendations(agent), [app.id, agent])
  const running = load.data?.latest?.status === 'running'
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void load.reload(), 2000)
    return () => clearInterval(timer)
  }, [running])

  const start = async (e: Event) => {
    const el = e.currentTarget as Element
    const tol = Number(tolerance.replace(',', '.'))
    if (tolerance.trim() === '' || Number.isNaN(tol) || tol < 0 || tol > 50) return toast(t('costs.cmp.notStarted'), t('costs.cmp.tolRange'), null)
    setStarting(true)
    try {
      const r = await api.admin.costs.compare(agent, { tolerance: tol })
      sparkle(el)
      toast(t('costs.cmp.started'), tn('costs.cmp.startedSub', r.candidates.length + 1))
      await load.reload()
    } catch (err) {
      const code = err instanceof EvokaiError ? err.code : ''
      if (code === 'comparison_running') {
        toast(t('costs.cmp.already'), t('costs.cmp.alreadySub'), null)
        await load.reload()
      } else
        toast(
          t('costs.cmp.notStarted'),
          code === 'no_cases' ? t('costs.cmp.noCases') : code === 'no_cheaper_model' ? t('costs.cmp.noCheaper') : describe(err),
          null,
        )
    } finally {
      setStarting(false)
    }
  }

  const switchTo = async (row: ModelComparisonRow, el: Element) => {
    setSwitching(row.model)
    try {
      const r = await api.admin.costs.switchModel(agent, row.model)
      done(el)
      const version = r.version.slice(0, 7)
      toast(
        r.created ? t('costs.cmp.switched', { model: modelName(row.model) }) : t('costs.cmp.alreadyUses', { model: modelName(row.model) }),
        r.publishState === 'pending' ? t('costs.cmp.held', { version }) : r.created ? t('costs.cmp.newVersion', { version }) : t('common.justNow'),
      )
      await load.reload()
      p.onSwitched()
    } catch (err) {
      toast(t('costs.cmp.notSwitched'), describe(err), null)
    } finally {
      setSwitching(null)
    }
  }

  return (
    <Panel
      title={t('costs.cmp.title')}
      actions={
        <span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <label class="affix" title={t('costs.cmp.tolHint')}>
            <span>{t('costs.cmp.tolerance')}</span>
            <input class="in num" style="width:64px;height:32px" inputMode="decimal" value={tolerance} onInput={(e) => setTolerance(e.currentTarget.value)} />
            <span>{t('costs.cmp.pts')}</span>
          </label>
          <Btn
            sm
            kind="pri"
            disabled={!p.hasCases || (load.data ? !load.data.candidates.length : true)}
            busy={starting ? t('evals.starting') : running ? t('evals.running') : false}
            onClick={(e) => void start(e)}
          >
            {t('costs.cmp.run')}
          </Btn>
        </span>
      }
    >
      <p class="hint" style="margin:0 0 12px">
        {t('costs.cmp.sub')}
      </p>
      <Loads load={load} what={t('what.models')} panel={false}>
        {(rec) => {
          const latest = rec.latest
          const rows = latest ? comparisonRows(latest.models) : []
          const best = latest?.recommended ? rows.find((r) => r.model === latest.recommended) : undefined
          return (
            <>
              <div class="mcnow">
                <span>
                  {t('costs.cmp.current')} {rec.current ? <b title={rec.current}>{modelName(rec.current)}</b> : <span class="s">-</span>}
                </span>
                <span>
                  {rec.candidates.length ? (
                    <>
                      {t('costs.cmp.cheaper')}{' '}
                      {rec.candidates.map((m) => (
                        <Tag key={m} title={m}>
                          {modelName(m)}
                        </Tag>
                      ))}
                    </>
                  ) : (
                    <span class="s">{t('costs.cmp.noCheaper')}</span>
                  )}
                </span>
              </div>
              {!p.hasCases ? <p class="hint">{t('costs.cmp.needsCases')}</p> : null}
              {!latest ? (
                <p class="hint">{t('costs.cmp.never')}</p>
              ) : (
                <>
                  <p class="hint" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                    {statusTag(latest.status)}
                    {t('costs.cmp.lastRun', { when: ago(latest.startedAt), tol: num(latest.tolerance) })}
                    {latest.cases != null ? ` ${tn('costs.cmp.cases', latest.cases)}` : ''}
                  </p>
                  {rec.current && latest.current && latest.current !== rec.current ? (
                    <p class="hint">{t('costs.cmp.stale', { then: modelName(latest.current), now: modelName(rec.current) })}</p>
                  ) : null}
                  {best && !best.current ? (
                    <div class="callout ok">
                      <span>
                        {t('costs.cmp.verdict', { model: modelName(best.model), cost: relativeCostText(best), rate: wholePct(best.passRate) })}
                      </span>
                      <span class="x">
                        <Btn
                          sm
                          kind="pri"
                          check
                          busy={switching === best.model ? t('costs.cmp.switching') : false}
                          onClick={(e) => void switchTo(best, e.currentTarget)}
                        >
                          {t('costs.cmp.switch')}
                        </Btn>
                      </span>
                    </div>
                  ) : latest.status === 'done' && rows.length ? (
                    <p class="hint">{t('costs.cmp.noneBetter')}</p>
                  ) : null}
                  {rows.length ? (
                    <div class="wx">
                      <table class="tbl">
                        <thead>
                          <tr>
                            <th>{t('costs.cmp.model')}</th>
                            <th class="r">{t('evals.passRate')}</th>
                            <th class="r">{t('costs.cmp.vsCurrent')}</th>
                            <th>{t('costs.cmp.cost')}</th>
                            <th class="r">{t('costs.cmp.latency')}</th>
                            <th>{t('costs.cmp.passes')}</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((r) => (
                            <tr key={r.model} aria-current={r.recommended ? 'true' : undefined}>
                              <td>
                                <b>{modelName(r.model)}</b>{' '}
                                {r.current ? <Tag>{t('costs.cmp.currentTag')}</Tag> : null}
                                {r.recommended ? (
                                  <Tag tone="ok" dot>
                                    {t('costs.cmp.recommended')}
                                  </Tag>
                                ) : null}
                                <div class="s">
                                  <code>{r.label}</code>
                                  {r.errors ? ` ${tn('costs.cmp.errors', r.errors)}` : ''}
                                </div>
                              </td>
                              <td class="num">{wholePct(r.passRate)}</td>
                              <td class="num">{r.current ? '-' : pointsText(r.passRateDelta)}</td>
                              <td style="min-width:180px">
                                <div class="s" style="margin-bottom:4px">
                                  {relativeCostText(r)}
                                </div>
                                <Bar value={bar(r.relativeCostPct)} />
                              </td>
                              <td class="num">{ms(r.latencyMs)}</td>
                              <td>
                                {r.current ? (
                                  <span class="s">-</span>
                                ) : r.passes ? (
                                  <Tag tone="ok" dot>
                                    {t('costs.cmp.yes')}
                                  </Tag>
                                ) : (
                                  <Tag tone="bad" dot>
                                    {t('costs.cmp.no')}
                                  </Tag>
                                )}
                              </td>
                              <td style="text-align:right">
                                {!r.current && r.passes ? (
                                  <Btn sm kind={r.recommended ? 'pri' : 'ghost'} busy={switching === r.model ? t('costs.cmp.switching') : false} onClick={(e) => void switchTo(r, e.currentTarget)}>
                                    {t('costs.cmp.switch')}
                                  </Btn>
                                ) : null}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                </>
              )}
            </>
          )
        }}
      </Loads>
    </Panel>
  )
}
