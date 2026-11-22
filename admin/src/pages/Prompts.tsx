/**
 * Prompts: the application's versioned prompts. A prompt module also has parameters (built here visually, or as raw
 * JSON Schema) and an optional data table; agents list the modules they use and the application's backend switches
 * and fills them per session. Every change is checked by the service as you type, and previewed with sample values.
 */
import { useEffect, useMemo, useState } from 'preact/hooks'

import type { AgentDef, ConsoleApplication, PromptVersion } from '@client/core/types'
import { describe, forApp } from '../api'
import { wordDiff } from '../diff'
import { dateTime, day } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { blankParam, builderToSchema, dataCheck, PARAM_TYPES, schemaToBuilder, type BuilderParam, type ParamType } from '../modules'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, KV, Panel, Seg, Switch, Tag } from '../ui'
import { Loads } from './common'
import { ParamsForm } from './ModuleParams'
import { num, t, type TextKey } from '../i18n'

const usedBy = (agents: AgentDef[], name: string): string[] =>
  agents.filter((a) => a.prompt?.name === name || (a.modules ?? []).some((m) => m.name === name)).map((a) => a.name)

/** The latest version of each prompt, by name. */
function latest(rows: PromptVersion[]): PromptVersion[] {
  const by = new Map<string, PromptVersion>()
  for (const r of rows) if ((by.get(r.name)?.version ?? 0) < r.version) by.set(r.name, r)
  return [...by.values()].sort((a, b) => a.name.localeCompare(b.name))
}

export function Prompts({ app, id }: { app: ConsoleApplication; id: string | null }) {
  setTitle(id ? (id === 'new' ? t('prm.new') : id) : t('tab.prompts'), app.name)
  const api = forApp(app.id)
  const list = useLoad(async () => {
    const [prompts, agents] = await Promise.all([api.admin.prompts.list(), api.admin.agents.list()])
    return { prompts, agents }
  }, [app.id])
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'prompts', 'new')}>
      {t('prm.add')}
    </Btn>
  )
  if (id)
    return (
      <Loads load={list} what={id === 'new' ? t('prm.new') : id}>
        {(d) => <Detail key={id} app={app} id={id} versions={d.prompts.filter((p) => p.name === id)} agents={d.agents} reload={list.reload} />}
      </Loads>
    )
  return (
    <>
      <Hero title={t('tab.prompts')} sub={t('prm.sub')} actions={add} />
      <Loads load={list} what={t('what.prompts')}>
        {({ prompts, agents }) => {
          const rows = latest(prompts)
          return rows.length ? (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('prm.prompt')}</th>
                      <th>{t('prm.description')}</th>
                      <th>{t('prm.kind')}</th>
                      <th>{t('prm.agents')}</th>
                      <th class="r">{t('prm.version')}</th>
                      <th class="r">{t('prm.updated')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => {
                      const by = usedBy(agents, p.name)
                      return (
                        <tr key={p.name} data-go onClick={() => go(appHref(app.slug, 'prompts', p.name))}>
                          <td>
                            <code>{p.name}</code>
                          </td>
                          <td class="s" style="max-width:420px">
                            {p.description}
                          </td>
                          <td>{p.parameters ? <Tag tone="vi">{t('prm.module')}</Tag> : <Tag>{t('prm.plain')}</Tag>}</td>
                          <td>{by.length ? by.map((x) => <Tag key={x}>{x}</Tag>) : <Tag>{t('prm.unused')}</Tag>}</td>
                          <td class="num">v{num(p.version)}</td>
                          <td class="num">{day(p.createdAt)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          ) : (
            <Panel>
              <Empty action={add}>
                <b>{t('prm.empty')}</b>
                <br />
                {t('prm.emptyText')}
              </Empty>
            </Panel>
          )
        }}
      </Loads>
    </>
  )
}

type Row = { key: string; text: string }
const toRows = (data: Record<string, string> | null): Row[] => Object.entries(data ?? {}).map(([key, text]) => ({ key, text }))
const fromRows = (rows: Row[]): Record<string, string> => Object.fromEntries(rows.filter((r) => r.key.trim()).map((r) => [r.key.trim(), r.text]))
const TYPE_LABEL: Record<ParamType, TextKey> = {
  choice: 'prm.type.choice',
  yesno: 'prm.type.yesno',
  number: 'prm.type.number',
  text: 'prm.type.text',
  multi: 'prm.type.multi',
}

function Detail(p: { app: ConsoleApplication; id: string; versions: PromptVersion[]; agents: AgentDef[]; reload: () => Promise<void> }) {
  const c = useConsole()
  const api = forApp(p.app.id)
  const isNew = p.id === 'new'
  const versions = [...p.versions].sort((a, b) => b.version - a.version)
  const cur = versions[0]
  const [name, setName] = useState(isNew ? '' : p.id)
  const [text, setText] = useState(cur?.text ?? '')
  const [description, setDescription] = useState(cur?.description ?? '')
  const [isModule, setIsModule] = useState(!!cur?.parameters)
  const initial = schemaToBuilder(cur?.parameters ?? null)
  const [params, setParams] = useState<BuilderParam[]>(initial ?? [])
  const [raw, setRaw] = useState(initial === null)
  const [rawText, setRawText] = useState(JSON.stringify(cur?.parameters ?? { type: 'object', properties: {} }, null, 2))
  const [rows, setRows] = useState<Row[]>(toRows(cur?.data ?? null))
  const [sample, setSample] = useState<Record<string, unknown>>({})
  const [preview, setPreview] = useState<{ text?: string; error?: string }>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [from, setFrom] = useState(versions[1]?.version ?? 0)
  const [to, setTo] = useState(cur?.version ?? 0)

  /** The schema as it stands, or an error when the raw JSON does not parse. */
  const schema = useMemo((): { value: Record<string, unknown> | null; error?: string } => {
    if (!isModule) return { value: null }
    if (!raw) return { value: builderToSchema(params) }
    try {
      return { value: JSON.parse(rawText) as Record<string, unknown> }
    } catch {
      return { value: null, error: t('prm.badJson') }
    }
  }, [isModule, raw, params, rawText])
  const data = isModule && rows.length ? fromRows(rows) : null
  const check = dataCheck(text, schema.value, data ?? {})

  // Live check and preview: the service checks the draft as a save would, and renders it with the sample values.
  const draftKey = JSON.stringify([text, schema.value, data, sample])
  useEffect(() => {
    if (!text.trim()) return setPreview({})
    if (schema.error) return setPreview({ error: schema.error })
    const timer = setTimeout(async () => {
      try {
        const out = await api.admin.prompts.preview(name || 'draft', { text, parameters: schema.value, data, params: sample })
        setPreview({ text: out })
      } catch (err) {
        setPreview({ error: describe(err) })
      }
    }, 450)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftKey])

  const switchRaw = (toRaw: boolean) => {
    if (toRaw) {
      setRawText(JSON.stringify(builderToSchema(params), null, 2))
      setRaw(true)
      return
    }
    try {
      const back = schemaToBuilder(JSON.parse(rawText))
      if (back === null) return setError(t('prm.builderCannot'))
      setParams(back)
      setRaw(false)
      setError(null)
    } catch {
      setError(t('prm.badJson'))
    }
  }

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setError(null)
    if (!/^[A-Za-z0-9_-]{1,80}$/.test(name)) return setError(t('prm.badName'))
    if (schema.error) return setError(schema.error)
    if (check.unknown.length) return setError(t('prm.dataUnknown', { keys: check.unknown.join(', ') }))
    setSaving(true)
    try {
      const version = await api.admin.prompts.put(name, { text, description, parameters: schema.value, data })
      sparkle(btn)
      done(btn)
      toast(t('prm.saved', { version }), name)
      await c.reloadApps()
      if (isNew) go(appHref(p.app.slug, 'prompts', name))
      else await p.reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }

  const setParam = (i: number, patch: Partial<BuilderParam>) => setParams(params.map((x, k) => (k === i ? { ...x, ...patch } : x)))
  const moveParam = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= params.length) return
    const next = [...params]
    ;[next[i], next[j]] = [next[j]!, next[i]!]
    setParams(next)
  }
  const importData = async (file: File | undefined) => {
    if (!file) return
    try {
      const parsed = JSON.parse(await file.text()) as unknown
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || !Object.values(parsed).every((v) => typeof v === 'string'))
        throw new Error('shape')
      setRows(toRows(parsed as Record<string, string>))
    } catch {
      setError(t('prm.dataBadFile'))
    }
  }
  const exportData = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(fromRows(rows), null, 2)], { type: 'application/json' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `${name || 'prompt'}-data.json`
    a.click()
    URL.revokeObjectURL(url)
  }
  const a = versions.find((v) => v.version === from)
  const b = versions.find((v) => v.version === to)
  const by = usedBy(p.agents, p.id)

  return (
    <>
      <Hero
        title={isNew ? t('prm.new') : p.id}
        sub={cur ? t('prm.subDetail', { version: cur.version, when: dateTime(cur.createdAt) }) : t('prm.newSub')}
        actions={
          <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
            {t('common.save')}
          </Btn>
        }
      />
      {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
      <div class="grid g-21">
        <Panel title={t('prm.text')}>
          <div class="grid g2" style="gap:0 14px">
            <Field label={t('common.name')} id="prm-name" hint={isNew ? t('prm.nameHint') : undefined}>
              <input id="prm-name" class="in mono" value={name} disabled={!isNew} onInput={(e) => setName(e.currentTarget.value.trim())} />
            </Field>
            <Field label={t('prm.description')} id="prm-desc">
              <input id="prm-desc" class="in" maxLength={500} value={description} onInput={(e) => setDescription(e.currentTarget.value)} />
            </Field>
          </div>
          <textarea class="in mono" spellcheck={false} style="min-height:220px" value={text} onInput={(e) => setText(e.currentTarget.value)} aria-label={t('prm.text')} />
          <p class="hint">{t(isModule ? 'prm.textHintModule' : 'prm.textHint')}</p>
        </Panel>
        <Panel title={t('prm.details')}>
          <KV
            rows={[
              [t('prm.kind'), isModule ? t('prm.module') : t('prm.plain')],
              [t('prm.agents'), by.join(', ') || t('common.none')],
              [t('prm.versions'), num(versions.length)],
            ]}
          />
          <Field label={t('prm.isModule')} hint={t('prm.isModuleHint')}>
            <Switch on={isModule} onChange={setIsModule} aria={t('prm.isModule')} />
          </Field>
        </Panel>
      </div>
      {isModule ? (
        <Panel
          title={t('prm.params')}
          actions={<Seg options={['builder', 'raw'] as const} labels={{ builder: t('prm.builder'), raw: t('prm.rawJson') }} value={raw ? 'raw' : 'builder'} onChange={(v) => switchRaw(v === 'raw')} />}
        >
          {raw ? (
            <textarea class="in mono" spellcheck={false} style="min-height:200px" value={rawText} onInput={(e) => setRawText(e.currentTarget.value)} aria-label={t('prm.rawJson')} />
          ) : (
            <>
              {params.length ? (
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('common.name')}</th>
                        <th>{t('prm.type')}</th>
                        <th>{t('prm.values')}</th>
                        <th>{t('prm.default')}</th>
                        <th>{t('prm.paramDescription')}</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {params.map((x, i) => (
                        <tr key={i}>
                          <td>
                            <input class="in mono" style="min-width:110px" value={x.name} aria-label={t('common.name')} onInput={(e) => setParam(i, { name: e.currentTarget.value.trim() })} />
                          </td>
                          <td>
                            <select class="in" value={x.type} aria-label={t('prm.type')} onChange={(e) => setParam(i, { ...blankParam(e.currentTarget.value as ParamType), name: x.name, description: x.description })}>
                              {PARAM_TYPES.map((k) => (
                                <option key={k} value={k}>
                                  {t(TYPE_LABEL[k])}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            {x.type === 'choice' || x.type === 'multi' ? (
                              <input
                                class="in"
                                value={x.values.join(', ')}
                                aria-label={t('prm.values')}
                                placeholder={t('prm.valuesHint')}
                                onChange={(e) => setParam(i, { values: e.currentTarget.value.split(',').map((v) => v.trim()).filter(Boolean) })}
                              />
                            ) : x.type === 'text' ? (
                              <input class="in" type="number" min={1} max={200} value={x.maxLength} aria-label={t('prm.maxLength')} onInput={(e) => setParam(i, { maxLength: Number(e.currentTarget.value) || 1 })} />
                            ) : x.type === 'number' ? (
                              <span class="flags">
                                <input class="in" style="width:80px" value={x.min} aria-label={t('prm.min')} placeholder="min" onInput={(e) => setParam(i, { min: e.currentTarget.value })} />
                                <input class="in" style="width:80px" value={x.max} aria-label={t('prm.max')} placeholder="max" onInput={(e) => setParam(i, { max: e.currentTarget.value })} />
                              </span>
                            ) : (
                              <span class="hint">{t('prm.noValues')}</span>
                            )}
                            {x.type === 'multi' ? (
                              <input class="in" type="number" min={1} max={200} value={x.maxItems} aria-label={t('prm.maxItems')} onInput={(e) => setParam(i, { maxItems: Number(e.currentTarget.value) || 1 })} />
                            ) : null}
                          </td>
                          <td>
                            {x.type === 'yesno' ? (
                              <select class="in" value={x.default} aria-label={t('prm.default')} onChange={(e) => setParam(i, { default: e.currentTarget.value })}>
                                <option value="">{t('prm.noDefault')}</option>
                                <option value="true">{t('mod.yes')}</option>
                                <option value="false">{t('mod.no')}</option>
                              </select>
                            ) : x.type === 'choice' ? (
                              <select class="in" value={x.default} aria-label={t('prm.default')} onChange={(e) => setParam(i, { default: e.currentTarget.value })}>
                                <option value="">{t('prm.noDefault')}</option>
                                {x.values.map((v) => (
                                  <option key={v} value={v}>
                                    {v}
                                  </option>
                                ))}
                              </select>
                            ) : (
                              <input class="in" value={x.default} aria-label={t('prm.default')} onInput={(e) => setParam(i, { default: e.currentTarget.value })} />
                            )}
                          </td>
                          <td>
                            <input class="in" value={x.description} aria-label={t('prm.paramDescription')} onInput={(e) => setParam(i, { description: e.currentTarget.value })} />
                          </td>
                          <td>
                            <span class="flags">
                              <Btn sm kind="ghost" label={t('prm.up')} onClick={() => moveParam(i, -1)}>
                                ↑
                              </Btn>
                              <Btn sm kind="ghost" label={t('prm.down')} onClick={() => moveParam(i, 1)}>
                                ↓
                              </Btn>
                              <Btn sm kind="ghost danger" onClick={() => setParams(params.filter((_, k) => k !== i))}>
                                {t('prm.removeParam')}
                              </Btn>
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p class="hint">{t('prm.noParamsYet')}</p>
              )}
              <div class="flags" style="margin-top:10px">
                <Btn sm onClick={() => setParams([...params, { ...blankParam('choice'), name: `param${params.length + 1}` }])}>
                  {t('prm.addParam')}
                </Btn>
              </div>
            </>
          )}
        </Panel>
      ) : null}
      {isModule ? (
        <Panel
          title={t('prm.data')}
          actions={
            <span class="flags">
              <label class="btn sm">
                <span class="lb">{t('prm.import')}</span>
                <input type="file" accept="application/json,.json" style="display:none" onChange={(e) => void importData(e.currentTarget.files?.[0])} />
              </label>
              <Btn sm onClick={exportData} disabled={!rows.length}>
                {t('prm.export')}
              </Btn>
            </span>
          }
        >
          <p class="hint">{check.allowed ? t('prm.dataKeysFrom', { keys: check.allowed.join(', ') }) : t('prm.dataHint')}</p>
          {rows.map((r, i) => {
            const unknown = check.unknown.includes(r.key.trim())
            return (
              <div key={i} class="grid" style="grid-template-columns:minmax(120px,220px) 1fr;gap:0 14px;margin-bottom:10px">
                <Field label={t('prm.key')} id={`prm-k-${i}`} hint={unknown ? t('prm.keyUnknown') : undefined}>
                  <input id={`prm-k-${i}`} class="in mono" value={r.key} aria-invalid={unknown} onInput={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, key: e.currentTarget.value } : x)))} />
                </Field>
                <Field label={t('prm.entry')} id={`prm-v-${i}`}>
                  <textarea id={`prm-v-${i}`} class="in" style="min-height:70px" value={r.text} onInput={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, text: e.currentTarget.value } : x)))} />
                  <Btn sm kind="ghost danger" onClick={() => setRows(rows.filter((_, k) => k !== i))}>
                    {t('prm.removeEntry')}
                  </Btn>
                </Field>
              </div>
            )
          })}
          {check.missing.length ? <p class="hint">{t('prm.dataMissing', { keys: check.missing.join(', ') })}</p> : null}
          <Btn sm onClick={() => setRows([...rows, { key: check.missing[0] ?? '', text: '' }])}>
            {t('prm.addEntry')}
          </Btn>
        </Panel>
      ) : null}
      <Panel title={t('prm.preview')}>
        {isModule && schema.value ? (
          <>
            <p class="hint">{t('prm.sampleHint')}</p>
            <ParamsForm schema={schema.value} values={sample} onChange={setSample} idPrefix="prm-s" />
          </>
        ) : null}
        {preview.error ? (
          <p class="hint" role="alert" style="color:var(--s-bad)">
            {preview.error}
          </p>
        ) : preview.text !== undefined ? (
          <pre class="raw" aria-label={t('prm.preview')}>
            {preview.text}
          </pre>
        ) : (
          <p class="hint">{t('prm.previewEmpty')}</p>
        )}
      </Panel>
      {versions.length ? (
        <Panel title={t('prm.history')}>
          <div class="wx">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t('prm.version')}</th>
                  <th>{t('prm.kind')}</th>
                  <th>{t('prm.description')}</th>
                  <th class="r">{t('prm.updated')}</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.version}>
                    <td class="num">v{num(v.version)}</td>
                    <td>{v.parameters ? <Tag tone="vi">{t('prm.module')}</Tag> : <Tag>{t('prm.plain')}</Tag>}</td>
                    <td class="s">{v.description}</td>
                    <td class="num">{dateTime(v.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {versions.length > 1 ? (
            <>
              <div class="flags" style="margin:12px 0">
                <label class="hint" for="prm-from">
                  {t('prm.compare')}
                </label>
                <select id="prm-from" class="in" style="width:auto" value={from} onChange={(e) => setFrom(Number(e.currentTarget.value))}>
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
                <label class="hint" for="prm-to">
                  {t('prm.with')}
                </label>
                <select id="prm-to" class="in" style="width:auto" value={to} onChange={(e) => setTo(Number(e.currentTarget.value))}>
                  {versions.map((v) => (
                    <option key={v.version} value={v.version}>
                      v{v.version}
                    </option>
                  ))}
                </select>
              </div>
              {a && b ? (
                <>
                  <div class="evdiff" aria-label={t('prm.diffLabel')}>
                    {wordDiff(a.text, b.text).map((d, i) =>
                      d.kind === 'same' ? <span key={i}>{d.text}</span> : d.kind === 'add' ? <ins key={i}>{d.text}</ins> : <del key={i}>{d.text}</del>,
                    )}
                  </div>
                  {JSON.stringify(a.parameters) !== JSON.stringify(b.parameters) ? <p class="hint">{t('prm.paramsChanged')}</p> : null}
                  {JSON.stringify(a.data) !== JSON.stringify(b.data) ? <p class="hint">{t('prm.dataChanged')}</p> : null}
                </>
              ) : null}
            </>
          ) : null}
        </Panel>
      ) : null}
    </>
  )
}
