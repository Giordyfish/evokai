/** Routes (in the URL hash, so the page path stays `{base}/admin/`), the console's shared state and data loading. */
import { createContext } from 'preact'
import { useCallback, useContext, useEffect, useRef, useState } from 'preact/hooks'

import type { ConsoleApplication, ConsoleMe } from '@client/core/types'
import { loggedOut } from './api'
import { progress } from './magic'
import { t } from './i18n'

export type Tab =
  | 'overview' | 'agents' | 'evals' | 'perms' | 'mcp' | 'connections' | 'channels' | 'skills' | 'knowledge' | 'workflows' | 'approvals' | 'keys' | 'limits'
  | 'usage' | 'traces' | 'conversations' | 'security' | 'privacy' | 'providers' | 'compliance' | 'audit'
export type Route =
  | { view: 'app'; app: string; tab: Tab; id: string | null; q: URLSearchParams }
  | { view: 'apps' | 'setup' | 'playground' | 'usage' | 'health' | 'access' | 'preferences' | 'login' | 'states' | 'missing'; q: URLSearchParams }

const TABS: Tab[] = ['overview', 'agents', 'evals', 'perms', 'mcp', 'connections', 'channels', 'skills', 'knowledge', 'workflows', 'approvals', 'keys', 'limits', 'usage', 'traces', 'conversations', 'security', 'privacy', 'providers', 'compliance', 'audit']

export function parseRoute(hash = location.hash): Route {
  const [path = '', query = ''] = hash.replace(/^#\/?/, '').split('?')
  const q = new URLSearchParams(query)
  const p = (path || 'apps').split('/').map(decodeURIComponent)
  if (p[0] === 'apps' && p[1]) {
    const tab = (p[2] || 'overview') as Tab
    if (!TABS.includes(tab)) return { view: 'missing', q }
    return { view: 'app', app: p[1], tab, id: p[3] ?? null, q }
  }
  const view = p[0] as Route['view']
  if (p[0] === 'me') return { view: 'preferences', q }
  if (['apps', 'setup', 'playground', 'usage', 'health', 'access', 'preferences', 'login'].includes(view)) return { view, q } as Route
  if (view === 'states' && import.meta.env.DEV) return { view, q }
  return { view: 'missing', q }
}

export const go = (hash: string): void => {
  if (location.hash === hash) dispatchEvent(new HashChangeEvent('hashchange'))
  else location.hash = hash
}

export const appHref = (slug: string, tab: Tab = 'overview', id?: string): string =>
  `#/apps/${encodeURIComponent(slug)}${tab === 'overview' && !id ? '' : `/${tab}`}${id ? `/${encodeURIComponent(id)}` : ''}`

export interface Console {
  me: ConsoleMe
  apps: ConsoleApplication[]
  canCreate: boolean
  reloadApps: () => Promise<void>
  /** The login ended: back to sign in. */
  signedOut: (why?: string) => void
}

export const ConsoleCtx = createContext<Console | null>(null)

export function useConsole(): Console {
  const c = useContext(ConsoleCtx)
  if (!c) throw new Error('outside the console')
  return c
}

export interface Loaded<T> {
  data: T | undefined
  error: unknown
  loading: boolean
  reload: () => Promise<void>
  set: (v: T) => void
}

/** Load data for a view: the top progress line runs meanwhile; a lost login goes back to sign in. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): Loaded<T> {
  const ctx = useContext(ConsoleCtx)
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<unknown>(undefined)
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)
  const run = useCallback(async () => {
    const n = ++seq.current
    setLoading(true)
    setError(undefined)
    progress('run')
    try {
      const v = await fn()
      if (n === seq.current) setData(v)
    } catch (e) {
      if (loggedOut(e)) ctx?.signedOut(t('common.sessionEnded'))
      if (n === seq.current) setError(e)
    } finally {
      progress('done')
      if (n === seq.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(() => {
    setData(undefined)
    void run()
  }, [run])
  return { data, error, loading, reload: run, set: setData }
}

/** The page title: `Permissions, Inkwell Books | Evokai Console`. */
export function setTitle(...parts: Array<string | null | undefined>): void {
  document.title = `${parts.filter(Boolean).join(', ')} | Evokai Console`
}
