/**
 * Limits: the caps (per day, week or month, for the application and for each user, in the application's currency),
 * retention, allowed origins and the limit messages. The caps are maxima an admin sets; what was spent shows only as a
 * share of each cap, never as an amount.
 */
import { useState } from 'preact/hooks'

import type { AppPatch, Application, CapPeriod, ConsoleApplication, LimitMessageKey } from '@client/core/types'
import { describe, forApp } from '../api'
import { CURRENCIES, type CapRow, capsPatch, newRow, PERIODS, rowsOf, usedOf, WEEKDAYS, weekdayName, withPeriod } from '../caps'
import { alertPatch, DEFAULT_THRESHOLDS, parseThresholds, thresholdsText, wholePct } from '../costs'
import { plain, sharePct } from '../format'
import { done, sparkle, toast } from '../magic'
import { setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, Panel, Switch, Tag } from '../ui'
import { Loads } from './common'
import { rich, t } from '../i18n'

const MESSAGE_KEYS: Array<[LimitMessageKey, () => string]> = [
  ['limit.app', () => t('limits.msg.app')],
  ['limit.appWeek', () => t('limits.msg.appWeek')],
  ['limit.appDay', () => t('limits.msg.appDay')],
  ['limit.subjectMonth', () => t('limits.msg.subjectMonth')],
  ['limit.subjectWeek', () => t('limits.msg.subjectWeek')],
  ['limit.subjectDay', () => t('limits.msg.subjectDay')],
]

export function Limits({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.limits'), app.name)
  const load = useLoad(() => forApp(app.id).admin.application.get(), [app.id])
  return (
    <Loads load={load} what={t('what.limits')}>
      {(a) => (
        <>
          <Form key={JSON.stringify(a)} app={app} a={a} reload={load.reload} />
          <EvalSettings key={`e${JSON.stringify(a)}`} app={app} a={a} reload={load.reload} />
          <AlertSettings key={`c${JSON.stringify(a)}`} app={app} a={a} reload={load.reload} />
        </>
      )}
    </Loads>
  )
}

/** The evals' own budget (apart from the caps), the rubric judge, runs on save and the test user's token. */
function EvalSettings({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const [budget, setBudget] = useState(plain(a.eval_monthly_usd ?? null))
  const [judge, setJudge] = useState(a.eval_judge_model ?? '')
  const [onSave, setOnSave] = useState(a.eval_on_save !== false)
  const [credential, setCredential] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const value = num(budget)
    if (value != null && (Number.isNaN(value) || value < 0)) return toast(t('limits.notSaved'), t('limits.evalBudgetPlain'), null)
    setBusy(true)
    try {
      await forApp(app.id).admin.application.patch({
        eval_monthly_usd: value,
        eval_judge_model: judge.trim() || null,
        eval_on_save: onSave,
        ...(credential ? { eval_credential: credential } : {}),
      })
      setCredential('')
      sparkle(btn)
      done(btn)
      toast(t('limits.evalSaved'), app.name)
      await reload()
    } catch (err) {
      toast(t('limits.notSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel
      title={t('tab.evals')}
      style="margin-top:16px"
      actions={
        <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
          {t('common.save')}
        </Btn>
      }
    >
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('limits.evalBudget')} id="ev-budget" hint={t('limits.evalBudgetHint')}>
          <input id="ev-budget" class="in num" inputMode="decimal" value={budget} onInput={(e) => setBudget(e.currentTarget.value)} />
        </Field>
        <Field label={t('limits.judge')} id="ev-judge" hint={t('limits.judgeHint')}>
          <input id="ev-judge" class="in mono" value={judge} placeholder="eu.anthropic.claude-haiku-4-5-20251001-v1:0" onInput={(e) => setJudge(e.currentTarget.value)} />
        </Field>
        <Field label={t('limits.onSave')}>
          <Switch on={onSave} onChange={setOnSave} label={onSave ? t('limits.onSaveRun') : t('limits.onSaveSkip')} />
        </Field>
        <Field label={t('limits.cred')} id="ev-cred" hint={a.hasEvalCredential ? t('limits.credKept') : t('limits.credNew')}>
          <input id="ev-cred" class="in mono" type="password" autocomplete="off" value={credential} onInput={(e) => setCredential(e.currentTarget.value)} />
        </Field>
      </div>
    </Panel>
  )
}

/**
 * Cost alerts: the shares of a cap a month-end forecast fires an alert at, and the webhook that receives them. The
 * webhook secret is write-only: the page shows whether one is stored, takes a new one, or removes it.
 */
function AlertSettings({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const [thresholds, setThresholds] = useState(thresholdsText(a.cost_alert_thresholds ?? DEFAULT_THRESHOLDS))
  const [webhook, setWebhook] = useState(a.cost_alert_webhook ?? '')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState<'save' | 'clear' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const parsed = parseThresholds(thresholds)
  const save = async (e: Event, clearSecret = false) => {
    const btn = e.currentTarget as Element
    // Removing the secret changes only the secret; the rest of the form is saved with Save.
    const r: ReturnType<typeof alertPatch> = clearSecret ? { ok: true, patch: { cost_alert_secret: '' } } : alertPatch({ thresholds, webhook, secret })
    if (!r.ok) return setError(t(r.error))
    if (clearSecret && !confirm(t('costs.hook.confirmClear'))) return
    setBusy(clearSecret ? 'clear' : 'save')
    setError(null)
    try {
      await forApp(app.id).admin.application.patch(r.patch)
      setSecret('')
      if (!clearSecret) {
        sparkle(btn)
        done(btn)
      }
      toast(t(clearSecret ? 'costs.hook.cleared' : 'costs.hook.saved'), app.name)
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(null)
    }
  }
  return (
    <Panel
      title={t('costs.hook.title')}
      style="margin-top:16px"
      actions={
        <Btn sm kind="pri" check busy={busy === 'save' ? t('common.saving') : false} onClick={(e) => void save(e)}>
          {t('common.save')}
        </Btn>
      }
    >
      <p class="hint" style="margin:0 0 12px">
        {t('costs.hook.sub')}
      </p>
      {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('costs.hook.thresholds')} id="ca-thr" hint={t('costs.hook.thresholdsHint')}>
          <input id="ca-thr" class="in mono" value={thresholds} placeholder="75, 90, 100" onInput={(e) => setThresholds(e.currentTarget.value)} />
          <div class="chips" aria-live="polite">
            {parsed.ok ? (
              (parsed.values ?? DEFAULT_THRESHOLDS).map((v) => (
                <Tag key={v} tone={v >= 100 ? 'bad' : 'warn'}>
                  {wholePct(v)}
                </Tag>
              ))
            ) : (
              <span class="hint" style="color:var(--s-bad);margin:0">
                {t(parsed.error)}
              </span>
            )}
            {parsed.ok && parsed.values == null ? <span class="hint" style="margin:0">{t('costs.hook.defaults')}</span> : null}
          </div>
        </Field>
        <Field label={t('costs.hook.address')} id="ca-hook" hint={rich('costs.hook.addressHint', { env: <code>EVOKAI_ALERT_WEBHOOK_ALLOW</code> })}>
          <input id="ca-hook" class="in mono" value={webhook} placeholder="https://hooks.example.com/evokai" autocomplete="off" spellcheck={false} onInput={(e) => setWebhook(e.currentTarget.value)} />
        </Field>
        <Field
          label={
            <>
              {t('costs.hook.secret')}{' '}
              <Tag tone={a.hasCostAlertSecret ? 'ok' : ''} dot>
                {t(a.hasCostAlertSecret ? 'costs.hook.set' : 'costs.hook.notSet')}
              </Tag>
            </>
          }
          id="ca-secret"
          hint={t(a.hasCostAlertSecret ? 'costs.hook.secretKept' : 'costs.hook.secretNew')}
        >
          <div style="display:flex;gap:8px">
            <input id="ca-secret" class="in mono" type="password" autocomplete="new-password" value={secret} onInput={(e) => setSecret(e.currentTarget.value)} />
            {a.hasCostAlertSecret ? (
              <Btn sm kind="ghost danger" busy={busy === 'clear' ? t('costs.hook.clearing') : false} onClick={(e) => void save(e, true)}>
                {t('costs.hook.clear')}
              </Btn>
            ) : null}
          </div>
        </Field>
        <div class="hint" style="margin-top:22px">
          {t('costs.hook.signedNote')}
        </div>
      </div>
    </Panel>
  )
}

const num = (s: string): number | null => (s.trim() === '' ? null : Number(s))

function Form({ app, a, reload }: { app: ConsoleApplication; a: Application; reload: () => Promise<void> }) {
  const c = useConsole()
  const [rows, setRows] = useState<CapRow[]>(() => rowsOf(a))
  const [currency, setCurrency] = useState(a.currency ?? 'USD')
  const [rate, setRate] = useState(plain(a.exchange_rate ?? null))
  const [ttl, setTtl] = useState(a.conversation_ttl_days == null ? '' : String(a.conversation_ttl_days))
  const [origins, setOrigins] = useState<string[]>(a.allowed_origins.length ? a.allowed_origins : [])
  const [messages, setMessages] = useState<Partial<Record<LimitMessageKey, string>>>({ ...a.messages })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    const caps = capsPatch(rows, currency, rate)
    if (!caps.ok) {
      setError(t(caps.error))
      return
    }
    const days = num(ttl)
    if (days != null && (!Number.isInteger(days) || days < 1)) {
      setError(t('limits.ttlWhole'))
      return
    }
    const clean = Object.fromEntries(Object.entries(messages).filter(([, v]) => v && v.trim())) as AppPatch['messages']
    const patch: AppPatch = {
      ...caps.patch,
      conversation_ttl_days: days,
      allowed_origins: origins.map((o) => o.trim()).filter(Boolean),
      messages: clean,
    }
    setSaving(true)
    setError(null)
    try {
      await forApp(app.id).admin.application.patch(patch)
      sparkle(btn)
      done(btn)
      toast(t('limits.saved'), t('limits.savedSub'))
      await c.reloadApps()
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <Hero
        title={t('tab.limits')}
        sub={t('limits.sub')}
        actions={
          <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        }
      />
      {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
      <Caps app={app} timezone={a.timezone} rows={rows} setRows={setRows} currency={currency} setCurrency={setCurrency} rate={rate} setRate={setRate} />
      <div class="grid g2" style="margin-top:16px">
        <Panel title={t('limits.retention')}>
          <Field label={t('limits.ttl')} hint={t('limits.ttlHint')}>
            <div class="affix">
              <input class="in num" inputMode="numeric" value={ttl} placeholder={t('limits.keptPh')} onInput={(e) => setTtl(e.currentTarget.value)} />
              <span>{t('limits.daysAfter')}</span>
            </div>
          </Field>
        </Panel>
        <Panel title={t('limits.origins')}>
          {origins.map((o, i) => (
            <div key={i} style="display:flex;gap:8px;margin-bottom:8px">
              <input
                class="in mono"
                value={o}
                placeholder="https://app.example"
                onInput={(e) => setOrigins(origins.map((x, k) => (k === i ? e.currentTarget.value : x)))}
              />
              <Btn sm kind="ghost danger" onClick={() => setOrigins(origins.filter((_, k) => k !== i))}>
                {t('limits.remove')}
              </Btn>
            </div>
          ))}
          {!origins.length ? <p class="hint" style="margin-bottom:10px">{t('limits.noOrigins')}</p> : null}
          <Btn sm onClick={() => setOrigins([...origins, ''])}>
            {t('limits.addOrigin')}
          </Btn>
        </Panel>
        <Panel title={t('limits.messages')} actions={<Tag>{a.locale}</Tag>}>
          {MESSAGE_KEYS.map(([k, label]) => (
            <Field key={k} label={<><code>{k}</code> <span>{label()}</span></>} hint={messages[k] ? undefined : t('limits.localeDefault')}>
              <input
                class="in"
                value={messages[k] ?? ''}
                placeholder={a.messageDefaults?.[k] ?? ''}
                onInput={(e) => setMessages({ ...messages, [k]: e.currentTarget.value })}
              />
            </Field>
          ))}
        </Panel>
      </div>
    </>
  )
}

/**
 * The caps: the currency and its rate, then one row per cap (who it binds, its period, its maximum, its reset) with the
 * share used of its current window. Only maxima are shown; spend is a share, never an amount.
 */
function Caps(p: {
  app: ConsoleApplication
  timezone: string
  rows: CapRow[]
  setRows: (r: CapRow[]) => void
  currency: string
  setCurrency: (v: string) => void
  rate: string
  setRate: (v: string) => void
}) {
  const { rows, setRows } = p
  const code = p.currency.trim().toUpperCase() || 'USD'
  const set = (i: number, r: CapRow) => setRows(rows.map((x, k) => (k === i ? r : x)))
  return (
    <Panel
      title={t('limits.caps')}
      actions={
        <Btn sm onClick={() => setRows([...rows, newRow(rows)])}>
          {t('limits.addCap')}
        </Btn>
      }
    >
      <p class="hint" style="margin:0 0 12px">
        {t('limits.capsHint', { tz: p.timezone || 'UTC' })}
      </p>
      <div class="grid g2" style="gap:0 14px">
        <Field label={t('limits.currency')} id="cap-cur" hint={t('limits.currencyHint')}>
          <input id="cap-cur" class="in mono" list="cap-currencies" maxLength={3} value={p.currency} onInput={(e) => p.setCurrency(e.currentTarget.value.toUpperCase())} />
          <datalist id="cap-currencies">
            {CURRENCIES.map((x) => (
              <option key={x} value={x} />
            ))}
          </datalist>
        </Field>
        <Field label={t('limits.rate')} id="cap-rate" hint={t('limits.rateHint', { currency: code })}>
          <input id="cap-rate" class="in num" inputMode="decimal" value={p.rate} disabled={code === 'USD'} placeholder={code === 'USD' ? '1' : ''} onInput={(e) => p.setRate(e.currentTarget.value)} />
        </Field>
      </div>
      <table class="tbl" style="margin-top:8px">
        <thead>
          <tr>
            <th>{t('limits.capWho')}</th>
            <th>{t('limits.capPeriod')}</th>
            <th>{t('limits.capAmount', { currency: code })}</th>
            <th>{t('limits.capResets')}</th>
            <th>{t('limits.capUsed')}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const used = usedOf(p.app, r)
            return (
              <tr key={i}>
                <td>
                  <select class="in" aria-label={t('limits.capWho')} value={r.scope} onChange={(e) => set(i, { ...r, scope: e.currentTarget.value as CapRow['scope'] })}>
                    <option value="application">{t('limits.capApp')}</option>
                    <option value="subject">{t('limits.capUser')}</option>
                  </select>
                </td>
                <td>
                  <select class="in" aria-label={t('limits.capPeriod')} value={r.period} onChange={(e) => set(i, withPeriod(r, e.currentTarget.value as CapPeriod))}>
                    {PERIODS.map((x) => (
                      <option key={x} value={x}>
                        {t(`limits.period.${x}`)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input class="in num" inputMode="decimal" aria-label={t('limits.capAmount', { currency: code })} value={r.amount} onInput={(e) => set(i, { ...r, amount: e.currentTarget.value, fromUsd: false })} />
                  {r.fromUsd ? <div class="hint">{t('limits.wasUsd', { currency: code })}</div> : null}
                </td>
                <td>
                  {r.period === 'week' ? (
                    <select class="in" aria-label={t('limits.capResets')} value={r.resets} onChange={(e) => set(i, { ...r, resets: e.currentTarget.value })}>
                      {WEEKDAYS.map((d) => (
                        <option key={d} value={d}>
                          {weekdayName(d)}
                        </option>
                      ))}
                    </select>
                  ) : r.period === 'month' ? (
                    <input class="in num" inputMode="numeric" aria-label={t('limits.capMonthDayLabel')} title={t('limits.capMonthDayHint')} value={r.resets} onInput={(e) => set(i, { ...r, resets: e.currentTarget.value })} />
                  ) : (
                    <span class="hint">{t('limits.resetsDaily')}</span>
                  )}
                </td>
                <td class="num">
                  {used ? (
                    <span title={r.scope === 'subject' ? t('limits.usedTop') : undefined}>
                      {sharePct(used.usedPct)}
                      {r.scope === 'subject' ? <span class="hint"> {t('limits.usedTopShort')}</span> : null}
                    </span>
                  ) : (
                    <span class="hint">{t('limits.capNew')}</span>
                  )}
                </td>
                <td>
                  <Btn sm kind="ghost danger" onClick={() => setRows(rows.filter((_, k) => k !== i))}>
                    {t('limits.remove')}
                  </Btn>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {!rows.length ? <p class="hint">{t('limits.noCaps')}</p> : null}
      <p class="hint" style="margin:10px 0 0">
        {t('limits.tokenMayOverride')}
      </p>
    </Panel>
  )
}
