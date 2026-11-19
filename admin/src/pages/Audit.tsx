/**
 * The audit log: every change, who made it, when, and the state before and after (secrets masked, people's ids
 * hashed). Per application for its roles; across the deployment for its owners (on the Access page).
 */
import { useState } from 'preact/hooks'

import type { AuditEntry, AuditQuery, ConsoleApplication } from '@client/core/types'
import { describe, forApp, root } from '../api'
import { ago, csv, dateTime, download } from '../format'
import { Empty, toast } from '../magic'
import { setTitle, useLoad } from '../state'
import { Btn, Hero, Panel, Tag, type Tone } from '../ui'
import { Loads } from './common'
import { t, type TextKey } from '../i18n'

const ACTIONS: Array<[string, TextKey]> = [
  ['', 'audit.f.all'],
  ['agent.', 'audit.f.agents'],
  ['grants.', 'audit.f.perms'],
  ['key.', 'audit.f.keys'],
  ['mcp_server.', 'audit.f.mcp'],
  ['skill.', 'audit.f.skills'],
  ['prompt.', 'audit.f.prompts'],
  ['application.', 'audit.f.app'],
  ['security.', 'audit.f.security'],
  ['privacy.', 'audit.f.privacy'],
  ['console.', 'audit.f.console'],
  ['sso.', 'audit.f.sso'],
  ['console_user.', 'audit.f.users'],
  ['preferences.', 'audit.f.prefs'],
]

const kindTone = (a: string): Tone => (a.endsWith('delete') || a.endsWith('revoke') || a.includes('erase') || a.includes('failed') ? 'bad' : a.startsWith('console.') ? '' : 'vi')

function Change({ e }: { e: AuditEntry }) {
  const [open, setOpen] = useState(false)
  const has = e.before != null || e.after != null
  return (
    <>
      <tr data-go={has ? true : undefined} onClick={() => has && setOpen(!open)}>
        <td class="s" title={dateTime(e.at)}>
          {ago(e.at)}
        </td>
        <td>
          <Tag tone={kindTone(e.action)}>{e.action}</Tag>
          {e.application ? <div class="s">{e.application}</div> : null}
        </td>
        <td class="mono s" style="overflow-wrap:anywhere;max-width:260px">
          {e.target || '-'}
        </td>
        <td>
          {e.actor}
          <div class="s">
            {e.actorKind}
            {e.role ? ` · ${e.role}` : ''}
          </div>
        </td>
        <td class="s">{has ? t(open ? 'audit.hide' : 'audit.show') : ''}</td>
      </tr>
      {open ? (
        <tr>
          <td colSpan={5}>
            <div class="grid g2">
              <div>
                <div class="hint">{t('audit.before')}</div>
                <pre class="mono" style="white-space:pre-wrap;font-size:12px;max-height:360px;overflow:auto">{JSON.stringify(e.before, null, 2)}</pre>
              </div>
              <div>
                <div class="hint">{t('audit.after')}</div>
                <pre class="mono" style="white-space:pre-wrap;font-size:12px;max-height:360px;overflow:auto">{JSON.stringify(e.after, null, 2)}</pre>
              </div>
            </div>
            <p class="hint">
              {t('audit.from', { ip: e.ip || t('audit.unknownIp'), request: e.correlationId || '-' })}
            </p>
          </td>
        </tr>
      ) : null}
    </>
  )
}

/** The log with filters; `app` null shows the whole deployment (owners only). */
export function AuditLog({ app, filename }: { app: ConsoleApplication | null; filename: string }) {
  const [q, setQ] = useState<AuditQuery>({})
  const [more, setMore] = useState<AuditEntry[]>([])
  const [next, setNext] = useState<string | null>(null)
  const fetchPage = (extra: AuditQuery) =>
    app ? forApp(app.id).admin.audit({ ...q, ...extra }) : root.deployment.audit({ ...q, ...extra })
  const load = useLoad(async () => {
    const page = await fetchPage({ limit: 100 })
    setMore([])
    setNext(page.next)
    return page.items
  }, [app?.id, JSON.stringify(q)])
  const loadMore = async () => {
    if (!next) return
    try {
      const page = await fetchPage({ before: next, limit: 100 })
      setMore([...more, ...page.items])
      setNext(page.next)
    } catch (err) {
      toast(t('audit.notLoaded'), describe(err), null)
    }
  }
  return (
    <Panel
      title={t('audit.changes')}
      cls="flat"
      actions={
        <span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <select class="in" style="height:32px;width:auto" value={q.action ?? ''} onChange={(e) => setQ({ ...q, action: e.currentTarget.value || undefined })}>
            {ACTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {t(l)}
              </option>
            ))}
          </select>
          <input
            class="in"
            style="height:32px;width:180px"
            placeholder={t('audit.whoPh')}
            value={q.actor ?? ''}
            onChange={(e) => setQ({ ...q, actor: e.currentTarget.value.trim() || undefined })}
          />
        </span>
      }
    >
      <Loads load={load} what={t('what.auditLog')}>
        {(first) => {
          const items = [...first, ...more]
          return items.length ? (
            <div class="wx">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>{t('audit.when')}</th>
                    <th>{t('audit.what')}</th>
                    <th>{t('audit.target')}</th>
                    <th>{t('audit.who')}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {items.map((e) => (
                    <Change key={e.id} e={e} />
                  ))}
                </tbody>
              </table>
              <div style="padding:12px;display:flex;gap:8px;justify-content:center">
                {next ? (
                  <Btn sm onClick={() => void loadMore()}>
                    {t('audit.loadMore')}
                  </Btn>
                ) : null}
                <Btn
                  sm
                  kind="ghost"
                  onClick={() =>
                    download(
                      filename,
                      csv([
                        ['at', 'application', 'action', 'target', 'actor', 'kind', 'role', 'before', 'after'],
                        ...items.map((e) => [e.at, e.application, e.action, e.target, e.actor, e.actorKind, e.role, JSON.stringify(e.before), JSON.stringify(e.after)]),
                      ]),
                      'text/csv',
                    )
                  }
                >
                  {t('audit.exportCsv')}
                </Btn>
              </div>
            </div>
          ) : (
            <Empty k="ember">
              <b>{t('audit.noMatch')}</b>
            </Empty>
          )
        }}
      </Loads>
    </Panel>
  )
}

export function Audit({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.audit'), app.name)
  return (
    <>
      <Hero title={t('tab.audit')} sub={t('audit.sub')} />
      <p class="hint" style="margin-bottom:12px">{t('audit.retention')}</p>
      <AuditLog app={app} filename={`${app.slug}-audit.csv`} />
    </>
  )
}
