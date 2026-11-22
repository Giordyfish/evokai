/**
 * The console's access to the service: the headless client in cookie mode. The admin key is exchanged once for an
 * HttpOnly, SameSite=Strict cookie and never kept; every request sends the CSRF header `X-Evokai-Console: 1` and, for
 * an operator, the application it acts on.
 */
import { EvokaiConsole, EvokaiError } from '@client/server/index'
import { errorFrom } from '@client/core/http'
import type { ConsoleMe, KnowledgeUpload } from '@client/core/types'

import { apiBase } from './api-base'
import { t } from './i18n'

export const baseUrl = location.origin + apiBase(location.pathname)

let me: ConsoleMe | null = null
const clients = new Map<string, EvokaiConsole>()

/** The client without an application (login, me, applications, health). */
export const root = new EvokaiConsole({ baseUrl })

export function setMe(value: ConsoleMe | null): void {
  me = value
  clients.clear()
}

/** A client bound to one application (an operator names it; an admin key has only its own). */
export function forApp(applicationId: string): EvokaiConsole {
  let c = clients.get(applicationId)
  if (!c) {
    // An operator and a single sign-on login name the application; an admin key has only its own.
    c = new EvokaiConsole({ baseUrl, application: me && me.kind !== 'adm' ? applicationId : null })
    clients.set(applicationId, c)
  }
  return c
}

/** A message a person can read, from any error. */
export function describe(e: unknown): string {
  if (e instanceof EvokaiError) {
    if (e.code === 'network') return t('common.serviceNoAnswer')
    const problems = (e.body as { problems?: string[] } | undefined)?.problems
    if (problems?.length) return problems.map((p) => p[0]!.toUpperCase() + p.slice(1)).join('. ') + '.'
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}

/** Whether an error means the console login is over (expired, revoked key, logged out elsewhere). */
export const loggedOut = (e: unknown): boolean =>
  e instanceof EvokaiError && (e.status === 401 || e.code === 'console')

export { EvokaiError }

/**
 * Upload a knowledge document with progress (the headless client's `fetch` cannot report it): the body is the file,
 * sent with the console cookie and its CSRF header, like every other console call.
 */
export function uploadDocument(
  applicationId: string,
  base: string,
  file: File,
  onProgress: (share: number) => void,
): Promise<KnowledgeUpload> {
  const client = forApp(applicationId)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const q = new URLSearchParams({ filename: file.name })
    xhr.open('POST', `${baseUrl}/v1/admin/knowledge/${encodeURIComponent(base)}/documents?${q}`)
    xhr.withCredentials = true
    xhr.setRequestHeader('X-Evokai-Console', '1')
    if (client.application) xhr.setRequestHeader('X-Evokai-Application', client.application)
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      let body: unknown
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : undefined
      } catch {
        body = undefined
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as KnowledgeUpload)
      else reject(errorFrom(xhr.status, body))
    }
    xhr.onerror = () => reject(new EvokaiError(0, 'network', 'Network error'))
    xhr.send(file)
  })
}

/**
 * A JSON call to an admin route the headless client does not wrap yet (capability credentials): sent with the console
 * cookie, its CSRF header and the application an operator acts on, like every other console call.
 */
export async function adminJson<T>(applicationId: string, method: string, path: string, body?: unknown): Promise<T> {
  const client = forApp(applicationId)
  const headers: Record<string, string> = { 'X-Evokai-Console': '1' }
  if (client.application) headers['X-Evokai-Application'] = client.application
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let r: Response
  try {
    r = await fetch(`${baseUrl}${path}`, {
      method,
      credentials: 'include',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new EvokaiError(0, 'network', 'Network error')
  }
  const text = await r.text()
  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : undefined
  } catch {
    parsed = undefined
  }
  if (!r.ok) throw errorFrom(r.status, parsed)
  return parsed as T
}
