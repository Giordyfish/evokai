// `npm run demo`: the components on a sample page, against a local Evokai. A small backend inside the dev server plays
// your application's part: it holds the server key and mints session tokens for a demo user.
//
//   EVOKAI_URL=http://localhost:8787/agent EVOKAI_SERVER_KEY=evk_srv_... EVOKAI_AGENT=assistant npm run demo
//
// The application's allowed origins must include http://localhost:5174.
import { fileURLToPath } from 'node:url'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { defineConfig, type Plugin } from 'vite'

const client = fileURLToPath(new URL('../../typescript/src', import.meta.url))
const SUBJECT = process.env.EVOKAI_SUBJECT ?? 'demo-user'

function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (c: Buffer) => (raw += String(c)))
    req.on('end', () => resolve(raw ? (JSON.parse(raw) as Record<string, unknown>) : {}))
  })
}

function send(res: ServerResponse, status: number, value: unknown): void {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(value))
}

/** The demo's backend: POST /api/agent/token and GET /api/agent/conversations, with the server key. */
function backend(): Plugin {
  return {
    name: 'evokai-demo-backend',
    configureServer(server) {
      server.middlewares.use(async (req, res, next) => {
        if (!req.url?.startsWith('/api/agent/')) return next()
        const base = process.env.EVOKAI_URL ?? 'http://localhost:8787/agent'
        const key = process.env.EVOKAI_SERVER_KEY
        if (!key) return send(res, 500, { error: 'Set EVOKAI_SERVER_KEY (and EVOKAI_URL) to run the demo.' })
        const auth = { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }
        try {
          if (req.url === '/api/agent/token' && req.method === 'POST') {
            const { conversationId } = await body(req)
            const mint = (cid: unknown) =>
              fetch(`${base}/v1/session-tokens`, {
                method: 'POST',
                headers: auth,
                body: JSON.stringify({ subject: SUBJECT, agent: process.env.EVOKAI_AGENT ?? 'assistant', ...(cid ? { conversationId: cid } : {}) }),
              })
            let r = await mint(conversationId)
            let restarted = false
            // An expired or unknown conversation: start a new one (the panel then starts clean).
            if (!r.ok && conversationId && (r.status === 404 || r.status === 403)) {
              r = await mint(null)
              restarted = true
            }
            const out = (await r.json()) as Record<string, unknown>
            return send(res, r.status, r.ok ? { ...out, restarted } : out)
          }
          if (req.url.startsWith('/api/agent/conversations')) {
            const r = await fetch(`${base}/v1/conversations?subject=${encodeURIComponent(SUBJECT)}`, { headers: auth })
            return send(res, r.status, await r.json())
          }
          return send(res, 404, { error: 'not_found' })
        } catch (e) {
          return send(res, 502, { error: String(e) })
        }
      })
    },
  }
}

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  resolve: { alias: [{ find: /^@evokai\/evokai-client\/(.*)$/, replacement: `${client}/$1/index.ts` }] },
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  server: { port: 5174, strictPort: true, fs: { allow: ['../..'] } },
  plugins: [backend()],
})
