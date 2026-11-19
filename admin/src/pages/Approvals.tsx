/** Approvals: the decisions workflows wait for, with what will happen; approve or reject with a comment. */
import { useState } from 'preact/hooks'

import type { ConsoleApplication, WorkflowApproval } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, dateTime } from '../format'
import { Empty, sparkle, toast } from '../magic'
import { appHref, setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, Panel, Seg, Tag } from '../ui'
import { runHref } from '../workflows'
import { Loads } from './common'
import { t, type TextKey } from '../i18n'

const FILTERS = ['pending', 'approved', 'rejected', 'all'] as const
type Filter = (typeof FILTERS)[number]

export function Approvals({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.approvals'), app.name)
  const api = forApp(app.id)
  const [status, setStatus] = useState<Filter>('pending')
  const list = useLoad(() => api.admin.workflows.approvals({ status }), [app.id, status])
  const labels = { pending: t('ap.f.pending'), approved: t('ap.f.approved'), rejected: t('ap.f.rejected'), all: t('ap.f.all') }
  return (
    <>
      <Hero title={t('tab.approvals')} sub={t('ap.sub')} actions={<Seg options={FILTERS} value={status} onChange={setStatus} labels={labels} />} />
      <Loads load={list} what={t('what.approvals')}>
        {(items) =>
          items.length ? (
            <div class="aplist">
              {items.map((a) => (
                <Card key={a.id} app={app} a={a} onDecided={() => void list.reload()} />
              ))}
            </div>
          ) : (
            <Panel>
              <Empty k="blink">
                <b>{status === 'pending' ? t('ap.empty') : t('ap.emptyOther')}</b>
                <br />
                {t('ap.emptyText')}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}

function Card({ app, a, onDecided }: { app: ConsoleApplication; a: WorkflowApproval; onDecided: () => void }) {
  const c = useConsole()
  const api = forApp(app.id)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const decide = async (decision: 'approve' | 'reject', e: Event) => {
    const btn = e.currentTarget as Element
    setBusy(decision)
    try {
      await api.admin.workflows.decide(a.id, { decision, comment: comment.trim() || undefined })
      if (decision === 'approve') sparkle(btn)
      toast(decision === 'approve' ? t('ap.approved') : t('ap.rejected'), a.workflow, decision === 'approve' ? 'flame' : null)
      await c.reloadApps()
      onDecided()
    } catch (err) {
      toast(t('ap.notDecided'), describe(err), null)
    } finally {
      setBusy(null)
    }
  }
  const roles = a.approvers.roles?.length ? a.approvers.roles.join(', ') : 'operator'
  const groups = a.approvers.groups?.length ? a.approvers.groups.join(', ') : null
  const details = Object.entries(a.details ?? {})
  return (
    <Panel
      cls="apcard"
      title={
        <>
          <code>{a.workflow}</code>
          <small>{t('ap.step', { step: a.step })}</small>
        </>
      }
      actions={
        a.status === 'pending' ? (
          <Tag tone="gold" dot>
            {t('ap.expires', { when: dateTime(a.expiresAt ?? '') })}
          </Tag>
        ) : (
          <Tag tone={a.status === 'approved' ? 'ok' : a.status === 'rejected' ? 'warn' : ''} dot>
            {t(`ap.s.${a.status}` as TextKey)}
          </Tag>
        )
      }
    >
      <p class="apq">{a.question}</p>
      {a.summary.length ? (
        <>
          <p class="hint">{t('ap.willHappen')}</p>
          <ul class="apsum">
            {a.summary.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </>
      ) : null}
      {details.length ? (
        <dl class="apdet">
          {details.map(([k, v]) => [<dt key={`k${k}`}>{k}</dt>, <dd key={`v${k}`}>{typeof v === 'string' ? v : JSON.stringify(v)}</dd>])}
        </dl>
      ) : null}
      <p class="hint">
        {t('ap.who', { roles })}
        {groups ? ` ${t('ap.groups', { groups })}` : ''} · {a.createdAt ? ago(a.createdAt) : ''} ·{' '}
        <a href={runHref(app.slug, a.runId)}>{t('ap.openRun')}</a>
        {a.subjectKey ? (
          <>
            {' '}
            · <code>{a.subjectKey}</code>
          </>
        ) : null}
      </p>
      {a.status === 'pending' ? (
        <div class="apdo">
          <input
            class="in"
            value={comment}
            maxLength={2000}
            placeholder={t('ap.comment')}
            aria-label={t('ap.comment')}
            onInput={(e) => setComment(e.currentTarget.value)}
          />
          <Btn kind="ghost danger" busy={busy === 'reject' ? t('ap.rejecting') : false} onClick={(e) => void decide('reject', e)}>
            {t('ap.reject')}
          </Btn>
          <Btn kind="pri" fx="confirm" busy={busy === 'approve' ? t('ap.approving') : false} onClick={(e) => void decide('approve', e)}>
            {t('ap.approve')}
          </Btn>
        </div>
      ) : (
        <p class="hint">
          {t('ap.decidedBy', { who: a.decidedBy ?? '', via: t(`ap.via.${a.decidedVia ?? 'console'}` as TextKey), when: a.decidedAt ? dateTime(a.decidedAt) : '' })}
          {a.comment ? ` ${t('ap.commentQuoted', { comment: a.comment })}` : ''}
        </p>
      )}
      {a.status === 'pending' ? null : (
        <p class="hint">
          <a href={appHref(app.slug, 'workflows', a.workflow)}>{t('ap.openWorkflow')}</a>
        </p>
      )}
    </Panel>
  )
}
