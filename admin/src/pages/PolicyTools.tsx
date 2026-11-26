/**
 * What every policy has, an application's and the system policy alike: its versions (compare two, restore one), its
 * tests (run them, add, remove) and a copy from another policy. Saves refused by failing tests show the failures and
 * offer to save anyway.
 */
import { useState } from 'preact/hooks'

import { describe } from '../api'
import { ago, dateTime } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import {
  expectText,
  newTest,
  type Outcome,
  OUTCOMES,
  pathText,
  type PolicyApi,
  type PolicyChange,
  type PolicyTestResult,
  type SaveOptions,
  type Saved,
  TESTABLE,
  testFailures,
  valueText,
} from '../policy'
import { useLoad } from '../state'
import { Btn, Field, Panel, Seg, Tag } from '../ui'
import { Loads } from './common'
import { t, tn, type TextKey } from '../i18n'

const outcomeLabel = (o: Outcome): string => t(`pol.o.${o}` as TextKey)

/** The tests that made a save fail, what each expected and what it got. */
export function TestFailures({ failures, onForce, busy }: { failures: PolicyTestResult[]; onForce?: () => void; busy?: boolean }) {
  return (
    <div class="card" style="padding:12px;margin:8px 0;border-color:var(--s-bad)">
      <p class="hint" style="color:var(--s-bad);margin:0 0 8px">
        {tn('pol.nFailing', failures.length)}
      </p>
      <ul class="plain">
        {failures.map((f) => (
          <li key={f.id}>
            <b>{f.name}</b>{' '}
            <span class="s">
              {t('pol.expects')} <code>{expectText(f.expected)}</code> · {t('pol.got', { what: expectText({ outcome: f.got.outcome, controls: f.got.controls }) })}
            </span>
          </li>
        ))}
      </ul>
      {onForce ? (
        <Btn sm kind="ghost" busy={busy ? t('common.saving') : false} onClick={onForce}>
          {t('pol.anyway')}
        </Btn>
      ) : null}
    </div>
  )
}

/**
 * Run a save that tests may refuse: on a refusal keep the failures and the retry, which sends `force`.
 *
 * Returns the failures (null when none), the call to make and whether it runs.
 */
export function useGuardedSave<A>(save: (arg: A, force: boolean) => Promise<void>) {
  const [failures, setFailures] = useState<PolicyTestResult[] | null>(null)
  const [pending, setPending] = useState<{ arg: A } | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async (arg: A, force = false) => {
    setBusy(true)
    try {
      await save(arg, force)
      setFailures(null)
      setPending(null)
    } catch (err) {
      const f = testFailures(err)
      if (f) {
        setFailures(f)
        setPending({ arg })
      } else toast(t('sec.notSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  const force = pending ? () => void run(pending.arg, true) : undefined
  return { failures, force, busy, run }
}

function savedToast(s: Saved, title: TextKey, sub: string) {
  if (s.created) toast(t(title), sub)
  else toast(t('pol.nothingNew'), t('pol.version', { n: s.version }), null)
}

/** The versions of a policy: who saved each and when, the differences between two, and a restore. */
export function PolicyHistory({ api, canEdit, onChanged, stamp }: { api: PolicyApi; canEdit: boolean; onChanged: () => Promise<void> | void; stamp: unknown }) {
  const load = useLoad(() => api.versions(), [api, JSON.stringify(stamp)])
  const [pair, setPair] = useState<{ from: number; to: number } | null>(null)
  const [changes, setChanges] = useState<PolicyChange[] | null>(null)
  const restore = useGuardedSave<number>(async (n, force) => {
    const s = await api.restore(n, { force } satisfies SaveOptions)
    savedToast(s, 'pol.restored', t('pol.restoredSub', { n }))
    await load.reload()
    await onChanged()
  })
  const compare = async (from: number, to: number) => {
    setPair({ from, to })
    try {
      setChanges((await api.diff(from, to)).changes)
    } catch (err) {
      setChanges(null)
      toast(t('sec.notLoaded'), describe(err), null)
    }
  }
  return (
    <Loads load={load} what={t('pol.history')}>
      {({ items }) => {
        const latest = items[0]?.number ?? 0
        const from = pair?.from ?? items[1]?.number ?? latest
        const to = pair?.to ?? latest
        return (
          <Panel tour="policy-history" title={t('pol.history')} actions={latest ? <Tag>{t('pol.version', { n: latest })}</Tag> : undefined}>
            <p class="hint">{t('pol.historySub')}</p>
            {restore.failures ? <TestFailures failures={restore.failures} onForce={restore.force} busy={restore.busy} /> : null}
            {items.length ? (
              <>
                <div class="wx">
                  <table class="tbl">
                    <tbody>
                      {items.map((v) => (
                        <tr key={v.number}>
                          <td style="white-space:nowrap">
                            <b>{t('pol.version', { n: v.number })}</b> {v.number === latest ? <Tag tone="ok">{t('pol.current')}</Tag> : null}
                          </td>
                          <td class="s" title={dateTime(v.createdAt)}>
                            {ago(v.createdAt)} · {t('pol.by', { who: v.createdBy })}
                            {v.note ? <div>{v.note}</div> : null}
                          </td>
                          <td style="text-align:right">
                            {canEdit && v.number !== latest ? (
                              <Btn sm kind="ghost" busy={restore.busy ? t('common.saving') : false} onClick={() => void restore.run(v.number)}>
                                {t('pol.restore')}
                              </Btn>
                            ) : null}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {items.length > 1 ? (
                  <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-top:12px">
                    <Field label={t('pol.from')}>
                      <select class="in" value={from} onChange={(e) => setPair({ from: Number(e.currentTarget.value), to })}>
                        {items.map((v) => (
                          <option key={v.number} value={v.number}>
                            {t('pol.version', { n: v.number })}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label={t('pol.to')}>
                      <select class="in" value={to} onChange={(e) => setPair({ from, to: Number(e.currentTarget.value) })}>
                        {items.map((v) => (
                          <option key={v.number} value={v.number}>
                            {t('pol.version', { n: v.number })}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Btn sm onClick={() => void compare(from, to)}>
                      {t('pol.compare')}
                    </Btn>
                  </div>
                ) : null}
                {changes ? <Changes changes={changes} /> : null}
              </>
            ) : (
              <p class="hint">{t('pol.noVersions')}</p>
            )}
          </Panel>
        )
      }}
    </Loads>
  )
}

function Changes({ changes }: { changes: PolicyChange[] }) {
  if (!changes.length) return <p class="hint">{t('pol.noChanges')}</p>
  return (
    <div class="wx">
      <table class="tbl">
        <thead>
          <tr>
            <th>{t('pol.setting')}</th>
            <th>{t('pol.before')}</th>
            <th>{t('pol.after')}</th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c) => (
            <tr key={c.path.join('\u0000')}>
              <td>
                <code>{pathText(c.path)}</code> <Tag tone={c.change === 'removed' ? 'bad' : c.change === 'added' ? 'ok' : 'gold'}>{t(`pol.c.${c.change}` as TextKey)}</Tag>
              </td>
              <td class="s mono" style="overflow-wrap:anywhere">
                {valueText(c.before)}
              </td>
              <td class="s mono" style="overflow-wrap:anywhere">
                {valueText(c.after)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

type Pick = Outcome | 'any'
const PICKS: readonly Pick[] = ['any', ...OUTCOMES]
const pickLabels = (): Record<Pick, string> => ({ any: t('pol.any'), allowed: outcomeLabel('allowed'), flagged: outcomeLabel('flagged'), blocked: outcomeLabel('blocked') })

/** A policy's tests: run them, add one, remove one. */
export function PolicyTests({ api, canEdit, stamp }: { api: PolicyApi; canEdit: boolean; stamp: unknown }) {
  const load = useLoad(() => api.tests(), [api])
  const [results, setResults] = useState<Map<string, PolicyTestResult> | null>(null)
  const [summary, setSummary] = useState<{ passed: number; failed: number } | null>(null)
  const [running, setRunning] = useState(false)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [input, setInput] = useState('')
  const [overall, setOverall] = useState<Pick>('any')
  const [per, setPer] = useState<Record<string, Pick>>({})
  const [error, setError] = useState<string | null>(null)
  // A new policy version: earlier results no longer say anything.
  const [seen, setSeen] = useState(JSON.stringify(stamp))
  if (seen !== JSON.stringify(stamp)) {
    setSeen(JSON.stringify(stamp))
    setResults(null)
    setSummary(null)
  }

  const run = async (e: Event) => {
    const btn = e.currentTarget as Element
    setRunning(true)
    try {
      const r = await api.run()
      setResults(new Map(r.items.map((x) => [x.id, x])))
      setSummary({ passed: r.passed, failed: r.failed })
      if (!r.failed) sparkle(btn)
    } catch (err) {
      toast(t('sec.notLoaded'), describe(err), null)
    } finally {
      setRunning(false)
    }
  }
  const add = async (e: Event) => {
    const btn = e.currentTarget as Element
    const body = newTest(name, input, overall, per)
    if (!body.name || !body.input.trim()) return setError(t('pol.needsName'))
    if (!body.outcome && !body.controls) return setError(t('pol.needsExpect'))
    setError(null)
    try {
      await api.addTest(body)
      done(btn)
      toast(t('pol.added'), body.name)
      setName('')
      setInput('')
      setOverall('any')
      setPer({})
      setAdding(false)
      await load.reload()
    } catch (err) {
      setError(describe(err))
    }
  }
  const remove = async (id: string, label: string) => {
    try {
      await api.deleteTest(id)
      toast(t('pol.removed'), label)
      await load.reload()
    } catch (err) {
      toast(t('sec.notSaved'), describe(err), null)
    }
  }

  return (
    <Loads load={load} what={t('pol.tests')}>
      {({ items }) => (
        <Panel
          tour="policy-tests"
          title={t('pol.tests')}
          actions={
            <span style="display:flex;gap:8px;align-items:center">
              {summary ? <Tag tone={summary.failed ? 'bad' : 'ok'}>{t('pol.ran', summary)}</Tag> : null}
              {items.length ? (
                <Btn sm busy={running ? t('pol.run') : false} onClick={(e) => void run(e)}>
                  {t('pol.run')}
                </Btn>
              ) : null}
            </span>
          }
        >
          <p class="hint">{t('pol.testsSub')}</p>
          {items.length ? (
            <div class="wx">
              <table class="tbl">
                <tbody>
                  {items.map((x) => {
                    const r = results?.get(x.id)
                    return (
                      <tr key={x.id}>
                        <td>
                          <b>{x.name}</b>
                          <div class="s mono" style="overflow-wrap:anywhere;max-width:420px">
                            {x.input.length > 160 ? `${x.input.slice(0, 160)}…` : x.input}
                          </div>
                        </td>
                        <td class="s">
                          {t('pol.expects')} <code>{expectText(x)}</code>
                        </td>
                        <td>
                          {r ? (
                            <>
                              <Tag tone={r.passed ? 'ok' : 'bad'} dot>
                                {r.passed ? t('pol.passed') : t('pol.failed')}
                              </Tag>
                              {!r.passed ? <div class="s">{t('pol.got', { what: expectText(r.got) })}</div> : null}
                            </>
                          ) : null}
                        </td>
                        <td style="text-align:right">
                          {canEdit ? (
                            <Btn sm kind="ghost" onClick={() => void remove(x.id, x.name)}>
                              {t('pol.delete')}
                            </Btn>
                          ) : null}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty k="ember">
              <b>{t('pol.noTests')}</b>
            </Empty>
          )}
          {canEdit && !adding ? (
            <Btn sm kind="ghost" onClick={() => setAdding(true)}>
              {t('pol.add')}
            </Btn>
          ) : null}
          {canEdit && adding ? (
            <div style="margin-top:12px">
              {error ? <p class="hint" style="color:var(--s-bad)">{error}</p> : null}
              <Field label={t('pol.name')} id="pt-name">
                <input id="pt-name" class="in" maxLength={120} value={name} onInput={(e) => setName(e.currentTarget.value)} />
              </Field>
              <Field label={t('pol.input')} hint={t('pol.inputHint')}>
                <textarea class="in" style="min-height:80px" value={input} spellcheck={false} onInput={(e) => setInput(e.currentTarget.value)} />
              </Field>
              <Field label={t('pol.overall')} hint={t('pol.outcomes', { allowed: outcomeLabel('allowed'), flagged: outcomeLabel('flagged'), blocked: outcomeLabel('blocked') })}>
                <Seg options={PICKS} value={overall} labels={pickLabels()} onChange={setOverall} />
              </Field>
              <Field label={t('pol.perControl')}>
                <table class="tbl">
                  <tbody>
                    {TESTABLE.map((c) => (
                      <tr key={c}>
                        <td>
                          <code>{c}</code>
                        </td>
                        <td>
                          <Seg options={PICKS} value={per[c] ?? 'any'} labels={pickLabels()} onChange={(v) => setPer({ ...per, [c]: v })} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Field>
              <div style="display:flex;gap:8px">
                <Btn sm kind="pri" check onClick={(e) => void add(e)}>
                  {t('pol.add')}
                </Btn>
                <Btn sm kind="ghost" onClick={() => setAdding(false)}>
                  {t('common.cancel')}
                </Btn>
              </div>
            </div>
          ) : null}
        </Panel>
      )}
    </Loads>
  )
}

/** Copy another policy into this one: the system policy or an application's, among those the person can see. */
export function CopyPolicy({
  api,
  sources,
  onChanged,
}: {
  api: PolicyApi
  sources: Array<{ value: string; label: string; system?: boolean }>
  onChanged: () => Promise<void> | void
}) {
  const [source, setSource] = useState('')
  const copy = useGuardedSave<string>(async (value, force) => {
    const s = sources.find((x) => x.value === value)
    if (!s) return
    const saved = await api.copy(s.system ? { system: true } : { application: s.value }, { force })
    savedToast(saved, 'pol.copied', s.label)
    await onChanged()
  })
  if (!sources.length) return null
  return (
    <Panel title={t('pol.copy')}>
      <p class="hint">{t('pol.copySub')}</p>
      {copy.failures ? <TestFailures failures={copy.failures} onForce={copy.force} busy={copy.busy} /> : null}
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select class="in" style="max-width:320px" value={source} onChange={(e) => setSource(e.currentTarget.value)} aria-label={t('pol.pick')}>
          <option value="">{t('pol.pick')}</option>
          {sources.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <Btn sm disabled={!source} busy={copy.busy ? t('common.saving') : false} onClick={() => void copy.run(source)}>
          {t('pol.copyBtn')}
        </Btn>
      </div>
    </Panel>
  )
}
