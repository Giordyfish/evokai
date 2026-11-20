/**
 * Connections, as the person sees them: the accounts the agent may use on their behalf (Microsoft 365, Google
 * Workspace), each with its state, the account, what the agent may use and when it last did, with «Connect» and
 * «Disconnect»; and the card a tool shows when its provider is not connected yet. Built like the memory view (same
 * layout, same parts naming), every text from `strings.ts`, every colour from the theme tokens.
 */
import type { TargetedKeyboardEvent, VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import type { Connection, ConnectionProvider } from '@evokai/evokai-client/browser'
import { useUi } from './context'
import type { Controller, ControllerState } from './controller'
import { Icon } from './icons'
import { memoryDate } from './memory'
import { fill, type Strings } from './strings'

const FAMILIES: Record<string, keyof Strings> = {
  mail: 'connectionFamilyMail',
  calendar: 'connectionFamilyCalendar',
  files: 'connectionFamilyFiles',
}

function escape(e: KeyboardEvent, fn: () => void): void {
  if (e.key !== 'Escape') return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

function Row({ x, c, connecting }: { x: Connection; c: Controller; connecting: string | null }): VNode {
  const { s, lang, names } = useUi()
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const no = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (asking) no.current?.focus()
  }, [asking])
  const t = (text: string, v: Record<string, string> = {}) => fill(text, { title: names.title, app: names.app, provider: x.title, ...v })
  const state =
    x.status === 'connected' ? s.connectionConnected : x.status === 'expired' ? s.connectionExpired : s.connectionNotConnected
  const families = (x.families ?? []).map((f) => (FAMILIES[f] ? (s[FAMILIES[f]] as string) : f)).join(', ')
  const used = x.lastUsedAt ? memoryDate(x.lastUsedAt, lang) : ''
  const meta = [
    x.account && x.status !== 'not_connected' ? t(s.connectionAccount, { account: x.account }) : '',
    used ? t(s.connectionLastUsed, { when: used }) : '',
  ].filter(Boolean)
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      setAsking(false)
    } catch {
      setError(s.connectionFailed)
    } finally {
      setBusy(false)
    }
  }
  const waiting = connecting === x.provider
  return (
    <li class="mitem conn" part="connection-item" data-provider={x.provider} data-status={x.status}>
      <div class="mbody">
        <p class="mtext" part="connection-name">
          <span class="cdot" data-status={x.status} aria-hidden="true" />
          {x.title}
          <span class="cstate" part="connection-state">
            {state}
          </span>
        </p>
        {meta.length ? (
          <p class="mmeta" part="connection-meta">
            {meta.join(' · ')}
          </p>
        ) : null}
        {families ? (
          <p class="mmeta" part="connection-families">
            {t(s.connectionFamilies, { families })}
          </p>
        ) : null}
        {waiting ? (
          <p class="mmeta" part="connection-waiting" role="status">
            {s.connectionWaiting}
          </p>
        ) : null}
        {error ? (
          <p class="err" part="connection-error" role="alert">
            {error}
          </p>
        ) : null}
        {asking ? (
          <div class="mconfirm" part="connection-confirm" role="alertdialog" aria-label={t(s.disconnectConfirm)} onKeyDown={(e) => escape(e, () => setAsking(false))}>
            <p>{t(s.disconnectConfirm)}</p>
            <div class="end">
              <button ref={no} type="button" class="btn" onClick={() => setAsking(false)}>
                {s.cancel}
              </button>
              <button type="button" class="btn danger" part="connection-disconnect connection-confirm-yes" disabled={busy} onClick={() => void run(() => c.disconnect(x.provider as ConnectionProvider))}>
                {s.disconnect}
              </button>
            </div>
          </div>
        ) : null}
      </div>
      {!asking ? (
        <div class="mact">
          {x.status === 'connected' ? (
            <button type="button" class="btn quiet" part="connection-disconnect" disabled={busy} onClick={() => setAsking(true)}>
              {s.disconnect}
            </button>
          ) : (
            <button
              type="button"
              class="btn primary"
              part="connection-connect"
              disabled={busy || waiting}
              onClick={() => void run(() => c.connect(x.provider as ConnectionProvider))}
            >
              {x.status === 'expired' ? s.reconnect : s.connect}
            </button>
          )}
        </div>
      ) : null}
    </li>
  )
}

export interface ConnectionsViewProps {
  controller: Controller
  state: ControllerState
  /** Back to the conversation (the panel gives the focus back). */
  onClose: () => void
}

/** «Connections»: replaces the conversation in the panel until the person goes back. */
export function ConnectionsView({ controller: c, state: st, onClose }: ConnectionsViewProps): VNode {
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  const title = useRef<HTMLHeadingElement>(null)
  useEffect(() => title.current?.focus(), [])
  const items = st.connections
  const loading = !items && !st.connectionsError
  const onKey = (e: TargetedKeyboardEvent<HTMLElement>) => {
    if (!e.defaultPrevented) escape(e, onClose)
  }
  return (
    <section class="memory" part="connections-view" aria-labelledby="ek-ctitle" aria-busy={loading} onKeyDown={onKey}>
      <div class="mhead">
        <button type="button" class="ib" part="connections-back" aria-label={s.memoryBack} title={s.memoryBack} onClick={onClose}>
          <Icon d="back" />
        </button>
        <h2 id="ek-ctitle" class="mtitle" part="connections-title" tabIndex={-1} ref={title}>
          {s.connectionsTitle}
        </h2>
      </div>
      <div class="mscroll">
        {st.connectionsError ? (
          <div class="mstate" part="connections-error">
            <p>{s.connectionsLoadFailed}</p>
            <button type="button" class="btn" onClick={() => void c.readConnections()}>
              {s.retry}
            </button>
          </div>
        ) : loading ? (
          <div class="mstate" role="status">
            <span class="spin" aria-hidden="true" />
            <span class="sub">{s.connectionsLoading}</span>
          </div>
        ) : (
          <>
            <p class="mintro sub">{t(s.connectionsIntro)}</p>
            {items && items.length === 0 ? (
              <p class="mstate sub" part="connections-empty">
                {s.connectionsEmpty}
              </p>
            ) : (
              <section class="mgroup">
                <ul part="connections-list">
                  {(items ?? []).map((x) => (
                    <Row key={x.provider} x={x} c={c} connecting={st.connecting} />
                  ))}
                </ul>
              </section>
            )}
          </>
        )}
      </div>
    </section>
  )
}

/** The card a tool shows when the person has not connected its provider: one button that starts the connection. */
export function ConnectCard(p: { provider: string; title: string; c: Controller; state: ControllerState }): VNode {
  const { s, names } = useUi()
  const [error, setError] = useState('')
  const t = (x: string) => fill(x, { title: names.title, app: names.app, provider: p.title })
  const now = p.state.connections?.find((x) => x.provider === p.provider)
  const done = now?.status === 'connected'
  const waiting = p.state.connecting === p.provider
  return (
    <div class="card connect" part="card connect-card" data-provider={p.provider} data-state={done ? 'connected' : 'open'} role="group" aria-label={t(s.connectCardTitle)}>
      <div class="ct">
        <Icon d="link" size={15} />
        {t(s.connectCardTitle)}
      </div>
      <p>{done ? t(s.connectCardDone) : waiting ? s.connectionWaiting : t(s.connectCardText)}</p>
      {error ? (
        <p class="err" role="alert">
          {error}
        </p>
      ) : null}
      {!done ? (
        <div class="end">
          <button
            type="button"
            class="chip on strong"
            part="connect-button"
            disabled={waiting}
            onClick={() => {
              setError('')
              p.c.connect(p.provider as ConnectionProvider).catch(() => setError(s.connectionFailed))
            }}
          >
            {s.connect}
          </button>
        </div>
      ) : null}
    </div>
  )
}
