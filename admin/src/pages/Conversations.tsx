/**
 * Conversations: the review of what people asked and what the agents answered (only for conversations kept in Evokai).
 * Browse with thumbs and notes filters, read one conversation with each turn's trace, and leave a reviewer's note or
 * label (kept, written to the audit log by label and length, never by text). On the assistant's system application
 * each person reads only their own conversations; an owner opens another person's only by an inspection with a
 * written reason, which the author is shown.
 */
import { useState } from 'preact/hooks'

import type { AdminConversation, AdminConversationDetail, AdminConversationQuery, ConsoleApplication, TraceSummary } from '@client/core/types'
import { adminJson, describe, EvokaiError, forApp } from '../api'
import { ago, dateTime, ms } from '../format'
import { Empty, toast } from '../magic'
import { appHref, go, setTitle, useLoad } from '../state'
import { labelText, LABELS } from '../traces'
import { Btn, Field, Hero, Panel, Tag } from '../ui'
import { Loads } from './common'
import { OutcomeTag } from './Traces'
import { num, t, tn } from '../i18n'

const queryOf = (q: URLSearchParams): AdminConversationQuery => ({
  agent: q.get('agent') || undefined,
  subject: q.get('subject') || undefined,
  feedback: (q.get('feedback') || undefined) as AdminConversationQuery['feedback'],
  noted: q.get('noted') === '1' || undefined,
})

export function Conversations({ app, id, q }: { app: ConsoleApplication; id: string | null; q: URLSearchParams }) {
  if (id) return <ConversationView app={app} id={id} focus={q.get('turn')} />
  setTitle(t('tab.conversations'), app.name)
  return (
    <>
      <Hero title={t('tab.conversations')} sub={t('cv.sub')} />
      <ConversationList app={app} q={q} />
    </>
  )
}

function ConversationList({ app, q }: { app: ConsoleApplication; q: URLSearchParams }) {
  const api = forApp(app.id)
  const query = queryOf(q)
  const [more, setMore] = useState<AdminConversation[]>([])
  const [next, setNext] = useState<string | null>(null)
  const set = (patch: Partial<AdminConversationQuery>) => {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries({ ...query, ...patch })) if (v) p.set(k, v === true ? '1' : String(v))
    go(`${appHref(app.slug, 'conversations')}${p.size ? `?${p.toString()}` : ''}`)
  }
  const agents = useLoad(async () => (await api.admin.agents.list()).map((a) => a.name), [app.id])
  const load = useLoad(async () => {
    const page = await api.admin.conversations.list({ ...query, limit: 50 })
    setMore([])
    setNext(page.next ?? null)
    return page
  }, [app.id, q.toString()])
  const loadMore = async () => {
    if (!next) return
    try {
      const page = await api.admin.conversations.list({ ...query, before: next, limit: 50 })
      setMore([...more, ...page.items])
      setNext(page.next ?? null)
    } catch (err) {
      toast(t('cv.notLoaded'), describe(err), null)
    }
  }
  return (
    <div class="grid" style="gap:16px">
      <Panel title={t('tr.filters')}>
        <div class="grid g3" style="gap:0 14px">
          <Field label={t('tr.agent')}>
            <select class="in" value={query.agent ?? ''} onChange={(e) => set({ agent: e.currentTarget.value || undefined })}>
              <option value="">{t('tr.any')}</option>
              {(agents.data ?? []).map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('tr.feedback')}>
            <select class="in" value={query.feedback ?? ''} onChange={(e) => set({ feedback: (e.currentTarget.value || undefined) as AdminConversationQuery['feedback'] })}>
              <option value="">{t('tr.any')}</option>
              <option value="down">{t('tr.fb.down')}</option>
              <option value="up">{t('tr.fb.up')}</option>
              <option value="any">{t('tr.fb.any')}</option>
            </select>
          </Field>
          <Field label={t('cv.subject')}>
            <input class="in mono" value={query.subject ?? ''} placeholder={t('tr.any')} onChange={(e) => set({ subject: e.currentTarget.value.trim() || undefined })} />
          </Field>
        </div>
        <label class="affix">
          <input type="checkbox" checked={!!query.noted} onChange={(e) => set({ noted: e.currentTarget.checked || undefined })} />
          <span>{t('cv.onlyNoted')}</span>
        </label>
      </Panel>
      <Loads load={load} what={t('what.conversations')}>
        {(page) => {
          if (page.storage === 'external')
            return (
              <Empty k="ember">
                <b>{t('cv.external')}</b>
                <br />
                {t('cv.externalText')}
              </Empty>
            )
          const items = [...page.items, ...more]
          return (
            <Panel cls="flat">
              {items.length ? (
                <div class="wx">
                  <table class="tbl">
                    <thead>
                      <tr>
                        <th>{t('cv.title')}</th>
                        <th>{t('tr.agent')}</th>
                        <th>{t('cv.subject')}</th>
                        <th class="r">{t('cv.turns')}</th>
                        <th>{t('tr.feedback')}</th>
                        <th class="r">{t('cv.notes')}</th>
                        <th>{t('cv.last')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((c) => (
                        <tr key={c.id} data-go onClick={() => go(appHref(app.slug, 'conversations', c.id))}>
                          <td>
                            {c.title === null ? <span class="s">{t('cv.othersConversation')}</span> : <b>{c.title}</b>}
                          </td>
                          <td>
                            {c.agent}
                            <div class="s">
                              <code>{c.agentVersion}</code>
                            </div>
                          </td>
                          <td class="s mono" style="max-width:220px;overflow-wrap:anywhere">
                            {c.subject}
                          </td>
                          <td class="num">{num(c.turns ?? 0)}</td>
                          <td>
                            <span style="display:flex;gap:4px">
                              {c.thumbsUp ? <Tag tone="ok">{t('cv.up', { n: c.thumbsUp })}</Tag> : null}
                              {c.thumbsDown ? <Tag tone="bad">{t('cv.down', { n: c.thumbsDown })}</Tag> : null}
                            </span>
                          </td>
                          <td class="num">{c.notes ? num(c.notes) : ''}</td>
                          <td class="s" title={dateTime(c.updatedAt)}>
                            {ago(c.updatedAt)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {next ? (
                    <div style="padding:12px;text-align:center">
                      <Btn sm onClick={() => void loadMore()}>
                        {t('audit.loadMore')}
                      </Btn>
                    </div>
                  ) : null}
                </div>
              ) : (
                <Empty k="ember">
                  <b>{t('cv.none')}</b>
                </Empty>
              )}
            </Panel>
          )
        }}
      </Loads>
    </div>
  )
}

function NoteForm({ app, conversation, turnId, onAdded }: { app: ConsoleApplication; conversation: string; turnId: string | null; onAdded: () => Promise<void> }) {
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const readOnly = app.role === 'viewer'
  const save = async () => {
    if (!label && !note.trim()) return
    setBusy(true)
    try {
      await forApp(app.id).admin.conversations.addNote(conversation, { turnId, label: label || null, note: note.trim() })
      setLabel('')
      setNote('')
      toast(t('cv.noted'), t('cv.notedSub'), null)
      await onAdded()
    } catch (err) {
      toast(t('cv.notNoted'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div>
      <Field label={t('cv.label')}>
        <select class="in" value={label} disabled={readOnly} onChange={(e) => setLabel(e.currentTarget.value)}>
          <option value="">{t('cv.noLabel')}</option>
          {LABELS.map((l) => (
            <option key={l} value={l}>
              {labelText(l)}
            </option>
          ))}
        </select>
      </Field>
      <Field label={t('cv.note')}>
        <input class="in" value={note} disabled={readOnly} maxLength={4000} placeholder={t('cv.notePh')} onInput={(e) => setNote(e.currentTarget.value)} />
      </Field>
      <div>
        <Btn kind="pri" busy={busy ? t('cv.saving') : false} disabled={readOnly || (!label && !note.trim())} onClick={() => void save()}>
          {t('cv.add')}
        </Btn>
      </div>
    </div>
  )
}

function TurnTrace({ app, trace }: { app: ConsoleApplication; trace: TraceSummary }) {
  return (
    <a href={appHref(app.slug, 'traces', trace.id)} style="display:inline-flex;align-items:center;gap:6px;text-decoration:none;color:inherit" title={t('cv.openTrace')}>
      <OutcomeTag x={trace} />
      <span class="mono">{ms(trace.durationMs)}</span>
      {trace.toolCalls ? <span class="s">{tn('cv.nTools', trace.toolCalls)}</span> : null}
      {trace.guardEvents ? <Tag tone="gold">{tn('tr.nGuard', trace.guardEvents)}</Tag> : null}
    </a>
  )
}

/** An owner's inspection of another person's conversation: a written reason first, then a read-only view. */
function InspectForm({ app, id, onDone }: { app: ConsoleApplication; id: string; onDone: (c: AdminConversationDetail) => void }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const ok = reason.trim().length >= 20
  const inspect = async () => {
    setBusy(true)
    try {
      onDone(await adminJson<AdminConversationDetail>(app.id, 'POST', `/v1/admin/assistant/conversations/${encodeURIComponent(id)}/inspect`, { reason: reason.trim() }))
    } catch (err) {
      toast(t('cv.notInspected'), describe(err), null)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Hero
        title={t('cv.inspectTitle')}
        sub={t('cv.inspectSub')}
        actions={
          <Btn kind="ghost" href={appHref(app.slug, 'conversations')}>
            {t('tr.back')}
          </Btn>
        }
      />
      <Panel cls="disclaimer">
        <p class="hint">{t('cv.inspectText')}</p>
        <Field label={t('cv.inspectReason')}>
          <textarea class="in" rows={3} maxLength={500} value={reason} placeholder={t('cv.inspectReasonPh')} onInput={(e) => setReason(e.currentTarget.value)} />
        </Field>
        <Btn kind="pri" disabled={!ok} busy={busy ? t('cv.inspecting') : false} onClick={() => void inspect()}>
          {t('cv.inspect')}
        </Btn>
      </Panel>
    </>
  )
}

function ConversationView({ app, id, focus }: { app: ConsoleApplication; id: string; focus: string | null }) {
  const api = forApp(app.id)
  const [inspected, setInspected] = useState<AdminConversationDetail | null>(null)
  const load = useLoad(async (): Promise<AdminConversationDetail | null> => {
    try {
      return await api.admin.conversations.get(id)
    } catch (err) {
      // Another person's conversation with the assistant: an owner opens it only by an inspection.
      if (err instanceof EvokaiError && err.code === 'inspection_required') return null
      throw err
    }
  }, [app.id, id])
  setTitle(t('cv.one'), app.name)
  const remove = async (noteId: string) => {
    try {
      await api.admin.conversations.deleteNote(id, noteId)
      await load.reload()
    } catch (err) {
      toast(t('cv.notDeleted'), describe(err), null)
    }
  }
  return (
    <Loads load={load} what={t('what.conversation')}>
      {(loaded: AdminConversationDetail | null) => {
        const c = loaded ?? inspected
        if (!c) return <InspectForm app={app} id={id} onDone={setInspected} />
        const traces = new Map((c.traces ?? []).map((x) => [x.id, x]))
        const notes = c.notes ?? []
        const shown = new Set<string>()
        return (
          <>
            <Hero
              title={c.title}
              sub={inspected ? t('cv.inspectedSub', { subject: c.subject }) : t('cv.oneSub', { agent: c.agent, version: c.agentVersion, subject: c.subject })}
              actions={
                <>
                  {inspected ? <Tag tone="warn">{t('cv.inspectedTag')}</Tag> : null}
                  <Btn kind="ghost" href={appHref(app.slug, 'conversations')}>
                    {t('tr.back')}
                  </Btn>
                </>
              }
            />
            <div class="grid g-21">
              <Panel title={t('cv.thread')}>
                <div class="thread" style="min-height:0">
                  {c.messages.map((m) => {
                    const trace = m.turnId && !shown.has(m.turnId) && m.role === 'assistant' ? traces.get(m.turnId) : undefined
                    if (trace) shown.add(trace.id)
                    const turnNotes = trace ? notes.filter((n) => n.turnId === trace.id) : []
                    return (
                      <div key={m.id} style={{ display: 'contents' }}>
                        <div class={`msg ${m.role === 'user' ? 'me' : 'ai'}`} style={{ whiteSpace: 'pre-wrap', outline: m.role === 'assistant' && m.turnId && m.turnId === focus ? '2px solid var(--vi)' : undefined }}>
                          {m.text || <span class="s">{t('cv.noText')}</span>}
                          {m.interrupted ? <div class="s">{t('cv.interrupted')}</div> : null}
                        </div>
                        {m.role === 'assistant' && (trace || m.feedback != null) ? (
                          <div class="who" style="flex-wrap:wrap">
                            {trace ? <TurnTrace app={app} trace={trace} /> : null}
                            {trace ? (
                              <a class="s" href={`${appHref(app.slug, 'conversations', c.id)}?turn=${encodeURIComponent(trace.id)}`}>
                                {t('cv.noteThis')}
                              </a>
                            ) : null}
                            {m.feedback != null ? <Tag tone={m.feedback > 0 ? 'ok' : 'bad'}>{m.feedback > 0 ? t('tr.fb.upShort') : t('tr.fb.downShort')}</Tag> : null}
                            {turnNotes.map((n) => (
                              <Tag key={n.id} tone="vi" title={n.note || undefined}>
                                {n.label ? labelText(n.label) : t('cv.noteTag')}
                              </Tag>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              </Panel>
              <div class="grid" style="gap:16px" hidden={!!inspected}>
                <Panel title={t('cv.review')}>
                  <p class="hint" style="margin:0 0 12px">{t('cv.reviewHint')}</p>
                  <NoteForm app={app} conversation={c.id} turnId={focus && traces.has(focus) ? focus : null} onAdded={load.reload} />
                  {focus && traces.has(focus) ? <p class="hint">{t('cv.onTurn')}</p> : null}
                </Panel>
                <Panel title={t('cv.notes')} actions={<Tag>{num(notes.length)}</Tag>}>
                  {notes.length ? (
                    notes.map((n) => (
                      <div key={n.id} class="opt">
                        <div class="t">
                          {n.label ? <Tag tone="vi">{labelText(n.label)}</Tag> : null} <b>{n.note}</b>
                          <small>
                            {n.author} · {ago(n.createdAt)}
                            {n.turnId ? ` · ${t('cv.onATurn')}` : ''}
                          </small>
                        </div>
                        {app.role !== 'viewer' ? (
                          <Btn sm kind="ghost" onClick={() => void remove(n.id)}>
                            {t('cv.delete')}
                          </Btn>
                        ) : null}
                      </div>
                    ))
                  ) : (
                    <p class="hint">{t('cv.noNotes')}</p>
                  )}
                </Panel>
              </div>
            </div>
          </>
        )
      }}
    </Loads>
  )
}
