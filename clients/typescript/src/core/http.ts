/**
 * Fetch plumbing shared by the server SDK and the browser session: JSON in and out, and typed errors from the
 * service's `{error, message}` bodies. Uses only the global `fetch` (Node ≥ 18, browsers, edge runtimes).
 */

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>

/** An error the service returned (or a transport failure, with `status` 0 and code `network`). */
export class EvokaiError extends Error {
  override readonly name: string = 'EvokaiError'
  /** HTTP status (0 when the request never got an answer). */
  readonly status: number
  /** Stable machine code from the service (see `KnownErrorCode`). */
  readonly code: KnownErrorCode | (string & {})
  /** Fields that failed validation, for `bad_request`. */
  readonly fields?: string[]
  /** The raw body, when it was JSON. */
  readonly body?: unknown

  constructor(status: number, code: string, message: string, extra: { fields?: string[]; body?: unknown } = {}) {
    super(message)
    this.status = status
    this.code = code
    if (extra.fields) this.fields = extra.fields
    if (extra.body !== undefined) this.body = extra.body
  }
}

/** Codes the service is known to return; others may appear. */
export type KnownErrorCode =
  | 'network' | 'unauthorized' | 'forbidden' | 'not_found' | 'bad_request' | 'conflict' | 'internal' | 'too_many_requests'
  | 'origin' | 'key' | 'operator_key' | 'console_header' | 'application_required' | 'unknown_application' | 'too_many_attempts'
  | 'unknown_conversation' | 'expired_conversation' | 'closed' | 'voice_engine' | 'self_revoke' | 'model' | 'slug_taken'
  | 'limit' | 'channel' | 'invalid_output' | 'no_exit_code' | 'wrong_code' | 'no_admin_key'
  | 'console' | 'login_code' | 'storage_mode' | 'grant' | 'not_granted' | 'secret'
  | 'channel_not_granted' | 'model_not_granted' | 'voice_engine_not_granted' | 'voice_not_granted' | 'stt_not_granted'
  | 'storage_mode_not_granted'

/** The conversation passed its retention (`conversation_ttl_days`): start a new one. */
export const isExpiredConversation = (e: unknown): boolean => e instanceof EvokaiError && e.code === 'expired_conversation'

export class EvokaiAuthError extends EvokaiError {
  override readonly name = 'EvokaiAuthError'
}
export class EvokaiNotFoundError extends EvokaiError {
  override readonly name = 'EvokaiNotFoundError'
}
export class EvokaiConflictError extends EvokaiError {
  override readonly name = 'EvokaiConflictError'
}
export class EvokaiValidationError extends EvokaiError {
  override readonly name = 'EvokaiValidationError'
}
export class EvokaiRateLimitError extends EvokaiError {
  override readonly name = 'EvokaiRateLimitError'
}

const STATUS_CODE: Record<number, string> = {
  400: 'bad_request', 401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 409: 'conflict', 422: 'bad_request',
  429: 'too_many_requests', 500: 'internal', 502: 'bad_gateway', 503: 'unavailable', 504: 'timeout',
}

/** Build the typed error for a status and a parsed body. */
export function errorFrom(status: number, body: unknown): EvokaiError {
  const b = body && typeof body === 'object' ? (body as Record<string, unknown>) : {}
  const code = typeof b.error === 'string' ? b.error : (STATUS_CODE[status] ?? 'error')
  const message = typeof b.message === 'string' ? b.message : `HTTP ${status}`
  const fields = Array.isArray(b.fields) ? b.fields.map(String) : undefined
  const extra = { fields, body }
  if (status === 401 || status === 403) return new EvokaiAuthError(status, code, message, extra)
  if (status === 404) return new EvokaiNotFoundError(status, code, message, extra)
  if (status === 409) return new EvokaiConflictError(status, code, message, extra)
  if (status === 400 || status === 422) return new EvokaiValidationError(status, code, message, extra)
  if (status === 429) return new EvokaiRateLimitError(status, code, message, extra)
  return new EvokaiError(status, code, message, extra)
}

/** Read an error response into a typed error. */
export async function errorFromResponse(res: Response): Promise<EvokaiError> {
  let body: unknown
  try {
    const text = await res.text()
    body = text ? JSON.parse(text) : undefined
  } catch {
    body = undefined
  }
  return errorFrom(res.status, body)
}

export function joinUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`
}

export type Query = Record<string, string | number | boolean | string[] | null | undefined>

/** A query string; a list repeats its key (`group=a&group=b`). */
export function withQuery(url: string, query?: Query): string {
  if (!query) return url
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) {
    if (Array.isArray(v)) for (const x of v) qs.append(k, x)
    else if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `${url}${url.includes('?') ? '&' : '?'}${s}` : url
}

export interface RequestOptions {
  method?: string
  query?: Query
  body?: unknown
  /** A body sent as is (a file upload), with its own `Content-Type` in `headers`. */
  rawBody?: Blob | ArrayBuffer | Uint8Array
  headers?: Record<string, string>
  signal?: AbortSignal
  /** Cookie mode (the admin console): `same-origin`. */
  credentials?: RequestCredentials
  /** `text`: resolve to the body as text (Markdown, HTML) instead of parsing JSON. */
  responseType?: 'json' | 'text'
}

/** One JSON request with a bearer credential. 204 resolves to `undefined`. */
export async function requestJson<T>(
  fetchFn: FetchLike,
  baseUrl: string,
  path: string,
  credential: string | null,
  opts: RequestOptions = {},
): Promise<T> {
  const accept = opts.responseType === 'text' ? 'text/markdown, text/html, text/plain' : 'application/json'
  const headers: Record<string, string> = { Accept: accept, ...(credential ? { Authorization: `Bearer ${credential}` } : {}), ...opts.headers }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  else if (opts.rawBody !== undefined && !headers['Content-Type']) headers['Content-Type'] = 'application/octet-stream'
  let res: Response
  try {
    res = await fetchFn(withQuery(joinUrl(baseUrl, path), opts.query), {
      method: opts.method ?? 'GET',
      headers,
      body: opts.rawBody !== undefined ? (opts.rawBody as BodyInit) : opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
      ...(opts.credentials ? { credentials: opts.credentials } : {}),
    })
  } catch (e) {
    if (isAbort(e)) throw e
    throw new EvokaiError(0, 'network', e instanceof Error ? e.message : 'Network error')
  }
  if (!res.ok) throw await errorFromResponse(res)
  if (res.status === 204) return undefined as T
  const text = await res.text()
  if (opts.responseType === 'text') return text as T
  return (text ? JSON.parse(text) : undefined) as T
}

export function isAbort(e: unknown): boolean {
  return !!e && typeof e === 'object' && (e as { name?: string }).name === 'AbortError'
}

export function defaultFetch(): FetchLike {
  if (typeof globalThis.fetch !== 'function') throw new Error('No global fetch: pass `fetch` in the options.')
  return (input, init) => globalThis.fetch(input, init)
}
