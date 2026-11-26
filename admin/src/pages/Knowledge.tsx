/**
 * Knowledge: the application's knowledge bases. Each one has its settings (embedding model, chunking, retrieval,
 * access groups, sources), its documents with their ingestion status, uploads with progress, a re-sync of its sources
 * and a test search that shows the ranked chunks with their scores and how an answer would cite them.
 */
import { useEffect, useRef, useState } from 'preact/hooks'

import type {
  ConsoleApplication,
  DocumentStatus,
  KnowledgeBase,
  KnowledgeBaseDetail,
  KnowledgeDocument,
  KnowledgeHit,
  KnowledgeList,
} from '@client/core/types'
import { describe, forApp, uploadDocument } from '../api'
import { Sources, Sup, where } from '../cite'
import { ago, bytes, pl } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { Bar, Btn, Field, Hero, Icon, Panel, Seg, Switch, Tag } from '../ui'
import { bodyOf, formOf, groups, type Form } from '../knowledge'
import { Loads } from './common'
import { num, t, tn, type TextKey } from '../i18n'

const STATUS_TONE: Record<DocumentStatus, 'ok' | 'bad' | 'vi' | undefined> = { ready: 'ok', failed: 'bad', ingesting: 'vi', queued: undefined }
const DIMS = ['256', '512', '1024'] as const

export function Knowledge({ app, id }: { app: ConsoleApplication; id: string | null }) {
  setTitle(id ? (id === 'new' ? t('kb.new') : id) : t('tab.knowledge'), app.name)
  const api = forApp(app.id)
  const list = useLoad(() => api.admin.knowledge.list(), [app.id])
  if (id)
    return (
      <Loads load={list} what={id === 'new' ? t('kb.new') : id}>
        {(l) =>
          id === 'new' || l.items.some((b) => b.name === id) ? (
            <Detail key={id} app={app} list={l} name={id === 'new' ? null : id} reloadList={list.reload} />
          ) : (
            <Panel>
              <Empty>
                <b>{t('kb.noBase', { name: id })}</b>
                <br />
                <a href={appHref(app.slug, 'knowledge')}>{t('kb.back')}</a>
              </Empty>
            </Panel>
          )
        }
      </Loads>
    )
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'knowledge', 'new')}>
      {t('kb.add')}
    </Btn>
  )
  return (
    <>
      <Hero title={t('tab.knowledge')} sub={t('kb.sub')} actions={add} />
      <Loads load={list} what={t('what.knowledge')}>
        {({ items }) =>
          items.length ? (
            <div class="grid g2">
              {items.map((b) => (
                <BaseCard key={b.name} b={b} onOpen={() => go(appHref(app.slug, 'knowledge', b.name))} />
              ))}
            </div>
          ) : (
            <Panel>
              <Empty action={add}>
                <b>{t('kb.empty')}</b>
                <br />
                {t('kb.emptyText')}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}

function BaseCard({ b, onOpen }: { b: KnowledgeBase; onOpen: () => void }) {
  return (
    <article class="panel appc" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === 'Enter' && onOpen()}>
      <div class="row">
        <span class="av all" style="border-radius:50%">
          <Icon name="knowledge" style="width:16px;height:16px" />
        </span>
        <div style="min-width:0">
          <h2 style="font-size:19px">{b.title || b.name}</h2>
          <code style="color:var(--mu);font-size:12px">{b.name}</code>
        </div>
      </div>
      {b.description ? (
        <p class="hint" style="margin:10px 0 0">
          {b.description}
        </p>
      ) : null}
      <div class="caps">
        <Tag tone="vi">{pl(b.documents, 'n.document')}</Tag>
        <Tag>{pl(b.chunks, 'n.chunk')}</Tag>
        {b.pending ? (
          <Tag tone="gold" dot>
            {t('kb.inProgress', { n: b.pending })}
          </Tag>
        ) : null}
        {b.failed ? (
          <Tag tone="bad" dot>
            {t('kb.failedN', { n: b.failed })}
          </Tag>
        ) : null}
        <Tag>{`${b.embeddingModel.replace(/^bedrock:/, '')}, ${b.dimensions}`}</Tag>
        {b.access.length ? <Tag tone="teal">{b.access.join(', ')}</Tag> : null}
      </div>
    </article>
  )
}

function Detail({ app, list, name, reloadList }: { app: ConsoleApplication; list: KnowledgeList; name: string | null; reloadList: () => Promise<void> }) {
  const c = useConsole()
  const api = forApp(app.id)
  const isNew = name === null
  const detail = useLoad<KnowledgeBaseDetail | null>(() => (name ? api.admin.knowledge.get(name) : Promise.resolve(null)), [app.id, name])
  const base = detail.data ?? null
  const [form, setForm] = useState<Form>(() => formOf(list.items.find((b) => b.name === name) ?? null, list))
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const set = (patch: Partial<Form>) => setForm({ ...form, ...patch })

  // While documents are queued or ingesting, their status is read again every two seconds.
  const pending = (base?.items ?? []).some((d) => d.status === 'queued' || d.status === 'ingesting')
  useEffect(() => {
    if (!pending || !name) return
    const id = setInterval(() => {
      api.admin.knowledge
        .get(name)
        .then((v) => detail.set(v))
        .catch(() => undefined)
    }, 2000)
    return () => clearInterval(id)
  }, [pending, name, api, detail])

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setSaving(true)
    try {
      const target = isNew ? form.name.trim() : (name as string)
      const r = await api.admin.knowledge.put(target, bodyOf(form, base))
      sparkle(btn)
      done(btn)
      toast(t('kb.saved'), r.reingested ? tn('kb.reingested', r.reingested) : target)
      await reloadList()
      await c.reloadApps()
      if (isNew) go(appHref(app.slug, 'knowledge', target))
      else await detail.reload()
    } catch (err) {
      toast(t('kb.notSaved'), describe(err), null)
    } finally {
      setSaving(false)
    }
  }
  const sync = async () => {
    if (!name) return
    setSyncing(true)
    try {
      const r = await api.admin.knowledge.sync(name)
      toast(t('kb.synced'), t('kb.syncResult', { ...r }), null)
      await detail.reload()
    } catch (err) {
      toast(t('kb.notSaved'), describe(err), null)
    } finally {
      setSyncing(false)
    }
  }
  const remove = async () => {
    if (!name || !confirm(t('kb.confirmRemove', { name }))) return
    try {
      await api.admin.knowledge.delete(name)
      toast(t('kb.removed'), name, null)
      await reloadList()
      go(appHref(app.slug, 'knowledge'))
    } catch (err) {
      toast(t('kb.notRemoved'), describe(err), null)
    }
  }
  const hasSources = !!(base?.sources.length)
  return (
    <>
      <Hero
        title={isNew ? t('kb.new') : base?.title || (name as string)}
        sub={isNew ? t('kb.newSub') : base?.description || name}
        actions={
          <>
            {!isNew && hasSources ? (
              <Btn busy={syncing ? t('kb.sync') : false} onClick={() => void sync()}>
                {t('kb.sync')}
              </Btn>
            ) : null}
            <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {isNew ? t('kb.add') : t('common.save')}
            </Btn>
          </>
        }
      />
      {!isNew ? (
        <Loads load={detail} what={t('what.knowledgeBase')}>
          {(d) =>
            d ? (
              <div class="grid" style="margin-bottom:16px">
                <Documents app={app} base={d} list={list} reload={detail.reload} />
                <TestSearch app={app} base={d} />
              </div>
            ) : null
          }
        </Loads>
      ) : null}
      <Settings form={form} set={set} list={list} isNew={isNew} />
      {!isNew ? (
        <div style="margin-top:12px">
          <Btn sm kind="ghost danger" onClick={() => void remove()}>
            {t('kb.remove')}
          </Btn>
        </div>
      ) : null}
    </>
  )
}

function Settings({ form, set, list, isNew }: { form: Form; set: (p: Partial<Form>) => void; list: KnowledgeList; isNew: boolean }) {
  const titan = form.embeddingModel.includes('titan-embed-text-v2')
  return (
    <div class="grid g3">
      <Panel title={t('kb.settings')}>
        {isNew ? (
          <Field label={t('kb.name')} id="kb-name" hint={t('kb.nameHint')}>
            <input id="kb-name" class="in mono" value={form.name} onInput={(e) => set({ name: e.currentTarget.value })} />
          </Field>
        ) : null}
        <Field label={t('kb.title')} id="kb-title">
          <input id="kb-title" class="in" value={form.title} onInput={(e) => set({ title: e.currentTarget.value })} />
        </Field>
        <Field label={t('kb.description')} id="kb-desc" hint={t('kb.descriptionHint')}>
          <textarea id="kb-desc" class="in" style="min-height:70px" value={form.description} onInput={(e) => set({ description: e.currentTarget.value })} />
        </Field>
        <Field label={t('kb.model')} id="kb-model" hint={t('kb.modelHint')}>
          <select id="kb-model" class="in mono" value={form.embeddingModel} onChange={(e) => set({ embeddingModel: e.currentTarget.value })}>
            {list.embeddingModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </Field>
        <div>
          <Field label={t('kb.dims')}>
            {titan ? (
              <Seg options={DIMS} value={String(form.dimensions) as (typeof DIMS)[number]} onChange={(v) => set({ dimensions: Number(v) })} />
            ) : (
              <input class="in num" type="number" min={16} max={4096} value={form.dimensions} onInput={(e) => set({ dimensions: Number(e.currentTarget.value) })} />
            )}
          </Field>
          <Field label={t('kb.language')} id="kb-lang">
            <select id="kb-lang" class="in" value={form.language} onChange={(e) => set({ language: e.currentTarget.value })}>
              {list.languages.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Panel>
      <Panel title={t('kb.retrieval')}>
        <div class="grid g2">
          <Field label={t('kb.topK')}>
            <input class="in num" type="number" min={1} max={20} value={form.topK} onInput={(e) => set({ topK: Number(e.currentTarget.value) })} />
          </Field>
          <Field label={t('kb.minScore')} hint={t('kb.minScoreHint')}>
            <input class="in num" type="number" min={0} max={1} step={0.05} value={form.minScore} onInput={(e) => set({ minScore: Number(e.currentTarget.value) })} />
          </Field>
        </div>
        <div class="flags" style="margin-bottom:14px">
          <Switch label={t('kb.hybrid')} on={form.hybrid} onChange={(v) => set({ hybrid: v })} />
          <Switch label={t('kb.mmr')} on={form.mmr} onChange={(v) => set({ mmr: v })} />
          <Switch label={t('kb.rerank')} on={form.rerank} onChange={(v) => set({ rerank: v })} />
        </div>
        <Field label={t('kb.access')} id="kb-access" hint={t('kb.accessHint')}>
          <input id="kb-access" class="in mono" value={form.access} onInput={(e) => set({ access: e.currentTarget.value })} />
        </Field>
        <Field label={t('kb.link')} id="kb-link" hint={t('kb.linkHint')}>
          <input id="kb-link" class="in mono" value={form.link} onInput={(e) => set({ link: e.currentTarget.value })} />
        </Field>
        <Field label={t('kb.retention')} id="kb-ret" hint={t('kb.retentionHint')}>
          <input id="kb-ret" class="in num" type="number" min={1} max={3650} value={form.retention} onInput={(e) => set({ retention: e.currentTarget.value })} />
        </Field>
      </Panel>
      <Panel title={t('kb.chunking')}>
        <div class="grid g2">
          <Field label={t('kb.chunkSize')}>
            <input class="in num" type="number" min={200} max={8000} step={100} value={form.maxChars} onInput={(e) => set({ maxChars: Number(e.currentTarget.value) })} />
          </Field>
          <Field label={t('kb.overlap')}>
            <input class="in num" type="number" min={0} max={2000} step={50} value={form.overlap} onInput={(e) => set({ overlap: Number(e.currentTarget.value) })} />
          </Field>
        </div>
        <Field label={t('kb.tableMax')}>
          <input class="in num" type="number" min={200} max={16000} step={100} value={form.tableMax} onInput={(e) => set({ tableMax: Number(e.currentTarget.value) })} />
        </Field>
        <p class="hint">{t('kb.chunkingNote')}</p>
        <div class="sources-h" style="margin-top:14px">
          {t('kb.sourcesTitle')}
        </div>
        <Field label={t('kb.urls')} id="kb-urls" hint={list.fetchAllowed.length ? t('kb.urlsHint', { domains: list.fetchAllowed.join(', ') }) : t('kb.urlsOff')}>
          <textarea id="kb-urls" class="in mono" style="min-height:60px" disabled={!list.fetchAllowed.length} value={form.urls} onInput={(e) => set({ urls: e.currentTarget.value })} />
        </Field>
        <Field label={t('kb.connectors')} hint={list.connectors.length ? t('kb.connectorsHint') : t('kb.noConnectors')}>
          <div class="flags">
            {list.connectors.map((s) => (
              <Switch
                key={s}
                label={s}
                on={form.connectors.includes(s)}
                onChange={(v) => set({ connectors: v ? [...form.connectors, s] : form.connectors.filter((x) => x !== s) })}
              />
            ))}
          </div>
        </Field>
      </Panel>
    </div>
  )
}

interface Upload {
  name: string
  share: number
  state: 'run' | 'ok' | 'same' | 'bad'
  error?: string
}

function Documents({ app, base, list, reload }: { app: ConsoleApplication; base: KnowledgeBaseDetail; list: KnowledgeList; reload: () => Promise<void> }) {
  const api = forApp(app.id)
  const [uploads, setUploads] = useState<Upload[]>([])
  const [over, setOver] = useState(false)
  const [url, setUrl] = useState('')
  const [fetching, setFetching] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  const send = async (files: File[]) => {
    const start = uploads.length
    let rows: Upload[] = [...uploads, ...files.map((f) => ({ name: f.name, share: 0, state: 'run' as const }))]
    setUploads(rows)
    const update = (i: number, patch: Partial<Upload>) => {
      rows = rows.map((r, k) => (k === start + i ? { ...r, ...patch } : r))
      setUploads(rows)
    }
    for (const [i, f] of files.entries()) {
      try {
        const r = await uploadDocument(app.id, base.name, f, (share) => update(i, { share }))
        update(i, { share: 1, state: r.outcome === 'unchanged' ? 'same' : 'ok' })
      } catch (err) {
        update(i, { state: 'bad', error: describe(err) })
      }
    }
    await reload()
  }
  const fetchUrl = async () => {
    if (!url.trim()) return
    setFetching(true)
    try {
      await api.admin.knowledge.addUrl(base.name, { url: url.trim() })
      toast(t('kb.fetched'), url.trim(), null)
      setUrl('')
      await reload()
    } catch (err) {
      toast(t('kb.notUploaded'), describe(err), null)
    } finally {
      setFetching(false)
    }
  }
  const reingest = async (d: KnowledgeDocument) => {
    try {
      await api.admin.knowledge.reingest(base.name, d.id)
      toast(t('kb.queuedAgain'), d.title || d.source, null)
      await reload()
    } catch (err) {
      toast(t('kb.notSaved'), describe(err), null)
    }
  }
  const remove = async (d: KnowledgeDocument) => {
    if (!confirm(t('kb.confirmDeleteDoc', { name: d.title || d.source }))) return
    try {
      await api.admin.knowledge.deleteDocument(base.name, d.id)
      toast(t('kb.docDeleted'), d.title || d.source, null)
      await reload()
    } catch (err) {
      toast(t('kb.notRemoved'), describe(err), null)
    }
  }
  const upLabel: Record<Upload['state'], string> = { run: '', ok: t('kb.uploaded'), same: t('kb.unchanged'), bad: t('kb.notUploaded') }
  return (
    <Panel title={t('kb.documents')} actions={<span class="hint" style="margin:0">{`${pl(base.documents, 'n.document')}, ${pl(base.chunks, 'n.chunk')}`}</span>}>
      <div
        class={`drop${over ? ' over' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const files = [...(e.dataTransfer?.files ?? [])]
          if (files.length) void send(files)
        }}
      >
        <span>{t('kb.drop')}</span>
        <Btn sm onClick={() => input.current?.click()}>
          {t('kb.choose')}
        </Btn>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          accept=".pdf,.docx,.pptx,.html,.htm,.md,.markdown,.txt,.csv"
          onChange={(e) => {
            const files = [...(e.currentTarget.files ?? [])]
            e.currentTarget.value = ''
            if (files.length) void send(files)
          }}
        />
        <span class="hint" style="margin:0">
          {t('kb.formats', { mb: list.maxFileMb })}
        </span>
      </div>
      {uploads.length ? (
        <ul class="uplist">
          {uploads.map((u, i) => (
            <li key={i}>
              <span class="nm">{u.name}</span>
              <Bar value={u.share * 100} />
              <span class="hint" style={`margin:0;${u.state === 'bad' ? 'color:var(--bad)' : ''}`}>
                {u.state === 'bad' ? (u.error ?? upLabel.bad) : upLabel[u.state]}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {list.fetchAllowed.length ? (
        <div class="row" style="gap:8px;margin-top:12px">
          <input class="in mono" placeholder={t('kb.urlPlaceholder')} value={url} onInput={(e) => setUrl(e.currentTarget.value)} />
          <Btn busy={fetching ? t('kb.fetch') : false} onClick={() => void fetchUrl()}>
            {t('kb.fetch')}
          </Btn>
        </div>
      ) : null}
      {base.items.length ? (
        <div class="wx" style="margin-top:14px">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('kb.col.document')}</th>
                <th>{t('kb.col.status')}</th>
                <th class="r">{t('kb.col.chunks')}</th>
                <th class="r">{t('kb.col.pages')}</th>
                <th>{t('kb.col.updated')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {base.items.map((d) => (
                <tr key={d.id}>
                  <td style="min-width:260px">
                    <div style="font-weight:600;overflow-wrap:anywhere">{d.title || d.source}</div>
                    <div class="s">
                      {`${t(`kb.src.${d.sourceKind}` as TextKey)}, ${bytes(d.size)}, v${d.version}`}
                      {d.access.length ? `, ${d.access.join(', ')}` : ''}
                    </div>
                    {d.status === 'failed' && d.reason ? <div class="reason">{d.reason}</div> : null}
                  </td>
                  <td>
                    <Tag tone={STATUS_TONE[d.status]} dot>
                      {t(`kb.st.${d.status}` as TextKey)}
                    </Tag>
                  </td>
                  <td class="num">{num(d.chunks)}</td>
                  <td class="num">{d.pages != null ? num(d.pages) : ''}</td>
                  <td class="s">{ago(d.updatedAt)}</td>
                  <td>
                    <div style="display:flex;gap:4px;justify-content:flex-end;flex-wrap:nowrap">
                      {d.url ? (
                        <Btn sm kind="ghost" href={d.url}>
                          {t('kb.open')}
                        </Btn>
                      ) : null}
                      <Btn sm kind="ghost" onClick={() => void reingest(d)}>
                        {t('kb.reingest')}
                      </Btn>
                      <Btn sm kind="ghost danger" onClick={() => void remove(d)}>
                        {t('kb.deleteDoc')}
                      </Btn>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p class="hint" style="margin-top:14px">
          {t('kb.noDocs')}
        </p>
      )}
    </Panel>
  )
}

function TestSearch({ app, base }: { app: ConsoleApplication; base: KnowledgeBase }) {
  const api = forApp(app.id)
  const [query, setQuery] = useState('')
  const [as, setAs] = useState('')
  const [busy, setBusy] = useState(false)
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const run = async (e?: Event) => {
    e?.preventDefault()
    if (!query.trim()) return
    setBusy(true)
    try {
      const r = await api.admin.knowledge.search(base.name, { query: query.trim(), groups: as.trim() ? groups(as) : null })
      setHits(r.items)
    } catch (err) {
      toast(t('kb.notSaved'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Panel title={t('kb.test')}>
      <form onSubmit={(e) => void run(e)}>
        <Field label={t('kb.query')} id="kb-q">
          <input id="kb-q" class="in" placeholder={t('kb.queryPlaceholder')} value={query} onInput={(e) => setQuery(e.currentTarget.value)} />
        </Field>
        <Field label={t('kb.asGroups')} id="kb-as" hint={t('kb.asGroupsHint')}>
          <input id="kb-as" class="in mono" value={as} onInput={(e) => setAs(e.currentTarget.value)} />
        </Field>
        <Btn kind="pri" type="submit" busy={busy ? t('kb.searching') : false}>
          {t('kb.search')}
        </Btn>
      </form>
      <p class="hint">{t('kb.testNote')}</p>
      {hits ? (
        hits.length ? (
          <div class="hits g2" style="display:grid">
            {hits.map((h) => (
              <div class="hit" key={h.index}>
                <div class="hd">
                  <span class="rank">{`${h.index}.`}</span>
                  <Tag tone="vi">{t('kb.relevance', { pct: `${num(Math.round(h.score * 100))}%` })}</Tag>
                  <Bar value={h.score * 100} style="width:80px" />
                  {h.similarity != null ? <Tag>{t('kb.similarity', { v: num(h.similarity, { maximumFractionDigits: 3 }) })}</Tag> : null}
                  {h.vectorRank != null ? <Tag>{t('kb.byMeaning', { n: h.vectorRank })}</Tag> : null}
                  {h.textRank != null ? <Tag tone="teal">{t('kb.byWords', { n: h.textRank })}</Tag> : null}
                  {h.kind === 'table' ? <Tag tone="gold">{t('kb.table')}</Tag> : null}
                </div>
                <div class="s" style="margin-bottom:6px">
                  {[h.document.title || h.document.source, where(h)].filter(Boolean).join(' · ')}
                </div>
                <div class="tx">{h.text}</div>
                <div class="pv">
                  <div class="sources-h">
                    {t('kb.preview')} <Sup c={h.citation} />
                  </div>
                  <Sources cites={[h.citation]} all bare />
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p class="hint">{t('kb.noResults')}</p>
        )
      ) : null}
    </Panel>
  )
}
