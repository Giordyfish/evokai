/** A scripted fetch: each route answers with a Response; every call is recorded. */
export interface Call {
  url: string
  method: string
  headers: Record<string, string>
  body: unknown
}

export type Handler = (call: Call, n: number) => Response | Promise<Response>

export function mockFetch(routes: Record<string, Handler | Handler[]>) {
  const calls: Call[] = []
  const counts: Record<string, number> = {}
  const fetchFn = async (input: string, init: RequestInit = {}): Promise<Response> => {
    const method = (init.method ?? 'GET').toUpperCase()
    const url = new URL(input)
    const headers: Record<string, string> = {}
    new Headers(init.headers).forEach((v, k) => {
      headers[k] = v
    })
    const raw = init.body
    const body = raw === undefined || raw === null ? undefined : typeof raw === 'string' ? JSON.parse(raw) : raw
    const call: Call = { url: input, method, headers, body }
    calls.push(call)
    const key = `${method} ${url.pathname}`
    const route = routes[key]
    if (!route) return json({ error: 'not_found', message: `no route ${key}` }, 404)
    const n = (counts[key] = (counts[key] ?? 0) + 1) - 1
    const handler = Array.isArray(route) ? (route[Math.min(n, route.length - 1)] as Handler) : route
    return handler(call, n)
  }
  return { fetch: fetchFn, calls }
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
}

/** An SSE response whose body is delivered in the given chunks (strings or bytes); `fail` errors the stream after them. */
export function sse(chunks: Array<string | Uint8Array>, opts: { turnId?: string; fail?: boolean } = {}): Response {
  const enc = new TextEncoder()
  let i = 0
  const stream = new ReadableStream<Uint8Array>({
    pull(ctrl) {
      if (i < chunks.length) {
        const c = chunks[i++] as string | Uint8Array
        ctrl.enqueue(typeof c === 'string' ? enc.encode(c) : c)
      } else if (opts.fail) ctrl.error(new TypeError('network error'))
      else ctrl.close()
    },
  })
  const headers: Record<string, string> = { 'Content-Type': 'text/event-stream' }
  if (opts.turnId) headers['X-Turn-Id'] = opts.turnId
  return new Response(stream, { status: 200, headers })
}

export function frame(event: object): string {
  return `event: ${String((event as { type?: unknown }).type)}\ndata: ${JSON.stringify(event)}\n\n`
}
