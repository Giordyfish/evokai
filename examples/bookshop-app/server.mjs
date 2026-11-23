// Inkwell Books: a tiny shop page with its assistant. One Node process, no framework:
//
//   GET  /                         the shop page (public/index.html)
//   GET  /vendor/...               the built @evokai/evokai-ui and @evokai/evokai-client browser files
//   GET  /api/books                the catalogue
//   POST /api/agent/token          a session token for the signed-in user (the server key stays here)
//   GET  /api/agent/conversations  the user's past conversations, for the panel's history
//   POST /mcp                      the shop's MCP server: one read-only tool, catalogue_search
//
// Settings come from the environment or from .env next to this file (written by `npm run setup`).

import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, extname, join, normalize, sep } from 'node:path'
import { timingSafeEqual } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { EvokaiServer, EvokaiError } from '@evokai/evokai-client/server'
import { books, search } from './catalogue.mjs'

const here = dirname(fileURLToPath(import.meta.url))
if (existsSync(join(here, '.env'))) process.loadEnvFile(join(here, '.env'))

const PORT = Number(process.env.PORT || 3000)
const EVOKAI_URL = process.env.EVOKAI_URL || 'http://localhost:8787/agent'
const AGENT = process.env.EVOKAI_AGENT || 'bookshop'
const MCP_SECRET = process.env.BOOKSHOP_MCP_SECRET || ''
if (!process.env.EVOKAI_SERVER_KEY) {
  console.error('EVOKAI_SERVER_KEY is not set: run `npm run setup` first (see README.md).')
  process.exit(1)
}

const evokai = new EvokaiServer({ baseUrl: EVOKAI_URL, serverKey: process.env.EVOKAI_SERVER_KEY })

// The browser files of the two packages, served as they are: the page maps the client's import with an import map.
const require = createRequire(import.meta.url)
const vendor = {
  'evokai-ui': join(dirname(require.resolve('@evokai/evokai-ui/package.json')), 'dist'),
  'evokai-client': join(dirname(require.resolve('@evokai/evokai-client/package.json')), 'dist'),
}
for (const [name, dir] of Object.entries(vendor)) {
  if (!existsSync(join(dir, 'index.js')) && !existsSync(join(dir, 'browser', 'index.js'))) {
    console.error(`${name} is not built (${dir}): see "Build the clients" in README.md.`)
    process.exit(1)
  }
}

// A real application takes the user from its own sign-in. Here every visitor is the same demo reader.
function currentUser() {
  return { id: 'reader-1' }
}

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.map': 'application/json' }

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function readJson(req) {
  let text = ''
  for await (const chunk of req) {
    text += chunk
    if (text.length > 100_000) throw new Error('body too large')
  }
  return text ? JSON.parse(text) : {}
}

async function sendFile(res, root, rel) {
  const path = normalize(join(root, rel))
  if (!path.startsWith(root + sep)) return json(res, 404, { error: 'not_found' })
  try {
    const body = await readFile(path)
    res.writeHead(200, { 'Content-Type': TYPES[extname(path)] || 'application/octet-stream' })
    res.end(body)
  } catch {
    json(res, 404, { error: 'not_found' })
  }
}

async function page(res) {
  const html = await readFile(join(here, 'public', 'index.html'), 'utf8')
  res.writeHead(200, { 'Content-Type': TYPES['.html'] })
  res.end(html.replaceAll('{{EVOKAI_URL}}', EVOKAI_URL).replaceAll('{{EVOKAI_AGENT}}', AGENT))
}

// The panel calls this on first use, when the token is about to expire and for another conversation.
async function token(req, res) {
  const { conversationId } = await readJson(req)
  // POST /api/agent/token?detailed=1 shows how the backend chooses prompt modules (the agent must list a session
  // module named tone, as config/bookshop.yaml does): the browser never sends them itself.
  const detailed = new URL(req.url, 'http://localhost').searchParams.has('detailed')
  const modules = detailed ? { tone: { params: { style: 'detailed' } } } : undefined
  const mint = (id) =>
    evokai.mintSessionToken({ subject: currentUser().id, agent: AGENT, conversationId: id, ttlSeconds: 900, modules })
  try {
    json(res, 200, await mint(conversationId || undefined))
  } catch (e) {
    if (!(e instanceof EvokaiError)) throw e
    // The page remembers its last conversation; when it is gone (expired, erased, a fresh Evokai), start a new one.
    if (conversationId && e.status === 404) return json(res, 200, { ...(await mint(undefined)), restarted: true })
    json(res, e.status || 502, { error: e.code, message: e.message })
  }
}

async function conversations(res) {
  json(res, 200, { items: await evokai.conversations.list({ subject: currentUser().id, limit: 30 }) })
}

// A minimal MCP server (Streamable HTTP, JSON answers). Evokai calls it with the secret it was registered with.
const SEARCH_TOOL = {
  name: 'catalogue_search',
  description: 'Search the Inkwell Books catalogue by title, author or genre. Returns id, title, author, genre, price in euros and copies in stock.',
  inputSchema: {
    type: 'object',
    properties: {
      query: { type: 'string', description: 'Words to look for; empty lists every book.' },
      in_stock_only: { type: 'boolean', description: 'Only books with copies in stock.' },
    },
  },
  annotations: { readOnlyHint: true },
}

function authorized(req) {
  const given = Buffer.from(req.headers.authorization || '')
  const expected = Buffer.from(`Bearer ${MCP_SECRET}`)
  return MCP_SECRET !== '' && given.length === expected.length && timingSafeEqual(given, expected)
}

async function mcp(req, res) {
  if (!authorized(req)) return json(res, 401, { error: 'unauthorized' })
  const msg = await readJson(req)
  if (msg.id === undefined) return res.writeHead(202).end() // a notification
  const reply = (result) => json(res, 200, { jsonrpc: '2.0', id: msg.id, result })
  switch (msg.method) {
    case 'initialize':
      return reply({
        protocolVersion: msg.params?.protocolVersion || '2025-06-18',
        capabilities: { tools: {} },
        serverInfo: { name: 'inkwell-books', version: '0.1.0' },
      })
    case 'tools/list':
      return reply({ tools: [SEARCH_TOOL] })
    case 'tools/call': {
      if (msg.params?.name !== SEARCH_TOOL.name) {
        return reply({ isError: true, content: [{ type: 'text', text: `Unknown tool: ${msg.params?.name}` }] })
      }
      const args = msg.params.arguments || {}
      const found = search(args.query, { inStockOnly: !!args.in_stock_only })
      return reply({ content: [{ type: 'text', text: JSON.stringify({ books: found }) }] })
    }
    default:
      return json(res, 200, { jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'Method not found' } })
  }
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost')
  const route = `${req.method} ${url.pathname}`
  try {
    if (route === 'GET /') return await page(res)
    if (route === 'GET /api/books') return json(res, 200, { items: books })
    if (route === 'POST /api/agent/token') return await token(req, res)
    if (route === 'GET /api/agent/conversations') return await conversations(res)
    if (route === 'POST /mcp') return await mcp(req, res)
    const m = /^\/vendor\/(evokai-ui|evokai-client)\/(.+)$/.exec(url.pathname)
    if (req.method === 'GET' && m) return await sendFile(res, vendor[m[1]], m[2])
    if (req.method === 'GET') return await sendFile(res, join(here, 'public'), url.pathname.slice(1))
    json(res, 404, { error: 'not_found' })
  } catch (e) {
    console.error(e)
    if (!res.headersSent) json(res, 500, { error: 'internal' })
  }
})

server.listen(PORT, () => {
  console.log(`Inkwell Books on http://localhost:${PORT} (Evokai at ${EVOKAI_URL}, agent ${AGENT})`)
})
