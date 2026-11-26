# @evokai/evokai-client

TypeScript client for Evokai. One package, three entry points:

| Import | Runs in | What it is |
| --- | --- | --- |
| `@evokai/evokai-client/server` | your backend (Node ≥ 18, edge, serverless) | `EvokaiServer`: session tokens, conversations, usage, memories, admin API. Uses only `fetch`. |
| `@evokai/evokai-client/browser` | any browser app | The headless core: session over HTTP + SSE with typed events and turn phases, replay and cancel, microphone, voice (dictation, conversation, barge-in), speech engines chosen by device, audio signals, usage as percentages. |
| `@evokai/evokai-client/react` | React ≥ 18 | Thin hooks: `EvokaiProvider`, `useEvokaiSession`, `usePhase`, `useUsage`, `useVoice`, `useAudioSignals`, `useDictation`. |

The client is **headless**: it draws nothing; your application builds its own interface. The full API (every
export, every event and its payload, the phase transitions) is in [API.md](./API.md).

The service authenticates applications with keys and browsers with short-lived session tokens:

1. your backend holds the **server key** and mints a **session token** for a user (an opaque `subject` of yours) and an agent;
2. the browser talks to Evokai directly with that token (the application's `allowed_origins` must include your site);
3. when the token expires or the user opens another conversation, the browser asks your backend for a new one.

`baseUrl` is where the service is mounted, base path included, e.g. `https://agents.example.com/agent`.

## Install

```sh
npm install @evokai/evokai-client
# React apps: react and react-dom ≥ 18 are peer dependencies.
# Server voice: npm install @pipecat-ai/client-js @pipecat-ai/small-webrtc-transport
```

## Backend: minting a session token

```ts
// evokai.ts
import { EvokaiServer } from '@evokai/evokai-client/server'

export const evokai = new EvokaiServer({
  baseUrl: process.env.EVOKAI_URL!, // https://agents.example.com/agent
  serverKey: process.env.EVOKAI_SERVER_KEY!, // srv_…
  adminKey: process.env.EVOKAI_ADMIN_KEY, // adm_…, only for evokai.admin.*
})
```

### Express-style route

```ts
import express from 'express'
import { EvokaiError } from '@evokai/evokai-client/server'
import { evokai } from './evokai'

const app = express()
app.use(express.json())

// The browser calls this whenever it needs a token: first use, expiry, or another conversation.
app.post('/api/agent/token', requireUser, async (req, res) => {
  try {
    const token = await evokai.mintSessionToken({
      subject: req.user.id, // your id for the user, opaque to Evokai
      agent: 'assistant',
      conversationId: req.body?.conversationId ?? undefined, // omitted = a new conversation
      tags: [req.user.plan],
      ttlSeconds: 900,
      limits: { caps: [{ period: 'week', amount: 5 }] }, // optional per-user caps (application currency), enforced by Evokai
      forwardCredential: req.user.accessToken, // for MCP servers with auth=forward_user
    })
    res.json(token) // { token, expiresAt, conversationId, voiceEngine, usage, memory }
  } catch (e) {
    if (e instanceof EvokaiError) res.status(e.status || 502).json({ error: e.code, message: e.message })
    else throw e
  }
})

// A history list for your interface: past conversations of this user.
app.get('/api/agent/conversations', requireUser, async (req, res) => {
  res.json(await evokai.conversations.list({ subject: req.user.id, limit: 30 }))
})
```

### Next.js route handler (App Router)

```ts
// app/api/agent/token/route.ts
import { NextResponse } from 'next/server'
import { EvokaiError } from '@evokai/evokai-client/server'
import { evokai } from '@/lib/evokai'
import { currentUser } from '@/lib/auth'

export async function POST(request: Request) {
  const user = await currentUser()
  if (!user) return NextResponse.json({ error: 'unauthorized' }, { status: 401 })
  const { conversationId } = (await request.json().catch(() => ({}))) as { conversationId?: string | null }
  try {
    const token = await evokai.mintSessionToken({ subject: user.id, agent: 'assistant', conversationId: conversationId ?? undefined })
    return NextResponse.json(token)
  } catch (e) {
    if (e instanceof EvokaiError) return NextResponse.json({ error: e.code, message: e.message }, { status: e.status || 502 })
    throw e
  }
}
```

Errors from the service arrive as typed errors (`EvokaiError` and its subclasses `EvokaiAuthError`,
`EvokaiNotFoundError`, `EvokaiConflictError`, `EvokaiValidationError`, `EvokaiRateLimitError`) with `status`, the
service's stable `code` (`unknown_conversation`, `closed`, `origin`, `limit`, …) and its `message`.

Other server calls: `evokai.conversations.get/delete`, `evokai.usage({ from, to, groupBy: ['subject', 'day'], tag })`,
`evokai.memories.list/forget`, `evokai.connections.list/disconnect`, `evokai.agents()`, `evokai.voices({ language })`, and with the admin key
`evokai.admin.application.get/patch` (caps per day, week or month in `caps` with `currency` and `exchange_rate`, the old `*_usd` caps, origins, models, `storage_mode`, `voice_engine`, `locale`, limit `messages`), `admin.keys`,
`admin.agents`, `admin.mcpServers`, `admin.skills` (`get` returns the body and the files; `files.get/put/delete`), `admin.prompts`, `admin.voices.put`,
`admin.bugReports.list`, `admin.keys.rotate(id)` (a successor shown once; the old key works until revoked),
`admin.grants.get/change` (capability grants, below), `admin.activity()`, `admin.usageSummary({ tag, agent })`,
`admin.redteam` (the built-in attack suite: `suite()`, `start(agent, { version, categories, languages })` (409
`redteam_running` while one is going), `runs(agent)`, `run(runId)` with the masked failures, and `publish(agent,
version, { force })`, the publish gate, audited), `admin.costs` (cost governance, shares only: `forecast()`,
`alerts({ period, open })`, `acknowledge(alertId)`, `cacheSuggestions()`, `compare(agent, { tolerance })` (409
`comparison_running`), `recommendations(agent)` and `switchModel(agent, model)`) and `admin.export()` (the application
as a config file).

### Long-term memory

Agents can remember three kinds of things about a person: how they like answers (`preference`), facts about their
work (`semantic`) and short summaries of finished conversations (`episodic`). The application's `memory_mode` decides
whether they may: `off`, `ask` (only people who said yes) or `on` (everyone except people who said no). Every answer
says where the person stands, as `memory: { mode, consent: 'granted' | 'declined' | 'unknown', active, ask }`: `active`
is true when memory is on for them now, `ask` when they should be asked (mode `ask`, no answer yet). A no, or
withdrawing, deletes what was remembered.

From your backend (server key):

```ts
// The person answered in your own interface: pass it with the token (the latest answer counts).
const token = await evokai.mintSessionToken({ subject: user.id, agent: 'assistant', memoryConsent: true })
token.memory // { mode: 'ask', consent: 'granted', active: true, ask: false }

await evokai.memories.consent(user.id, false) // { memory, deleted }: a no deletes everything remembered
const facts = await evokai.memories.list(user.id) // [{ id, fact, kind, text, source, createdAt, ... }]
await evokai.memories.forget(user.id) // everything, or forget(user.id, memoryId) for one
```

From the browser (session token), for your own interface; [`@evokai/evokai-ui`](../ui/README.md) draws all of it:

```ts
const { memory, items } = await session.memories() // items: Memory[] ({ id, text, kind, agent, source, dates, uses })
if (memory.ask) await session.memoryConsent(true) // { memory, deleted }; 409 `memory_off` when the mode is off
await session.editMemory(id, 'Prefers short answers') // 400 `memory_refused`: instructions, secrets or personal data
await session.forgetMemory(id) // one; forgetMemory() deletes everything. Resolves with how many were deleted.
```

`GET /v1/session` (`session.info()`) also carries `memory`. An edit needs memory on for the person (403
`memory_inactive`); `source` is `person` once they rewrote a memory.

### Capability grants

Each application is granted what it may use: models (as primary and fallback, fallback only, or off), MCP servers and
single tools, skills, client tools, browser and server speech with their engines and voices, storage modes and
channels. Your backend still makes the runtime choices (`voiceEngine`, `voice`, `storageMode`, `model` in
`mintSessionToken`); asking for something not granted fails with 403 and a code: `model_not_granted`,
`voice_engine_not_granted`, `voice_not_granted`, `stt_not_granted`, `storage_mode_not_granted`, `channel_not_granted`.

```ts
const view = await evokai.admin.grants.get()          // every item: default, override, value
await evokai.admin.grants.change([
  { kind: 'model', name: 'eu.amazon.nova-2-lite-v1:0', value: 'fallback' },
  { kind: 'tool', name: 'bookshop/order_refund', value: false },
  { kind: 'client', name: 'memory', value: null },    // back to the default
])
```

### Server runs (back-office agents)

An agent whose `channels` include `server` can be run from your backend, with no browser and no user in the loop:

```ts
const run = await evokai.runAgent('triage', {
  input: 'Classify this ticket',            // or [{ role: 'user', text }, …]
  attachments: [{ name: 'ticket.txt', mediaType: 'text/plain', text: ticketBody }],
  subject: 'system:triage',
  store: false,
})
run.output // schema-valid JSON when the agent has an output_schema, else text
run.usage  // { inputTokens, outputTokens, cacheReadTokens, cacheWriteTokens, costUsd, toolCalls }
           // inputTokens includes the prompt cache reads and writes
```

Errors: 403 `channel` (the agent does not allow server runs), 429 `limit`, 502 `invalid_output` (the answer did not
match `output_schema` after one retry). A token or chat for a conversation past `conversation_ttl_days` fails with 404
`expired_conversation` (`isExpiredConversation(e)`).

### Workflows and their approvals

Durable workflows ([docs/workflows.md](../../docs/workflows.md)) start from your backend, and their approval steps can be
shown and decided in your own interface, for your users (you vouch for their groups):

```ts
const run = await evokai.workflows.start('return-review', {
  input: { orderId: 1042, reason },
  subject: 'CU-7781',                      // whose data it is: erased with them
  idempotencyKey: 'return-1042',           // a repeated call returns the first run
})
const pending = await evokai.workflows.approvals({ groups: ['customer-service'] })
await evokai.workflows.decide(pending[0]!.id, { decision: 'approve', comment: 'ok', by: { name: 'Giulia Neri', groups: ['customer-service'] } })
const detail = await evokai.workflows.run(run.id) // status, steps, output; usage as capPct (a share of the cap)
```

In a chat, a `handoff` event (`{ from, to, title, reason }`) says the conversation goes on with another agent from the
next turn.

### The admin console client

`EvokaiConsole` (also in `/server`) is the client the admin console uses: `login(key)` exchanges an admin or
operator key for an httpOnly cookie (the key is not kept), and every other call (all of `EvokaiServer`, plus
`me()`, `applications()`, `health(check)`, `models()`, `parseSkill(text)`, `operator.applications.list/create`,
`operator.mcpTest({url, token})`, `preferences.get/put/reset` for the signed-in person's console preferences)
uses the cookie with
`X-Evokai-Console: 1`; an operator sets `console.application` to the application to act on. The login ends when its
admin key is revoked. The service never sends a console login an amount: `consoleUsage(q)` returns tokens, calls and
shares of the caps (`capPct`, `dayCapPct`, `subjectCapPct`, `subjectTodayPct`, and the application's month and day
used and left), and `applications()` carries shares too. `toUsageShares(report)` turns a server-key usage report into
shares and drops every amount, for any other interface that must not show money.

### Applications that keep the conversation themselves (`storage_mode: "external"`)

Send the history with each turn and append what comes back:

```ts
const result = await evokai.chatJson(token, { text: 'How many orders shipped this week?', history: await loadHistory(userId) })
await appendHistory(userId, result.messages ?? []) // the turn's new messages (HistoryMessage[])
```

In the browser the same contract is `getHistory` / `onMessages` on `EvokaiSession` (below).

## React: hooks for your own interface

```tsx
import { EvokaiProvider, useEvokaiSession, usePhase, useUsage, useVoice } from '@evokai/evokai-client/react'

const getToken = ({ conversationId }: { conversationId: string | null }) =>
  fetch('/api/agent/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversationId }),
  }).then((r) => r.json()) // { token, expiresAt, conversationId, voiceEngine, usage }

export function App() {
  return (
    <EvokaiProvider options={{ baseUrl: 'https://agents.example.com/agent', getToken }} voice={{ micSound: 'goccia' }}>
      <Assistant />
    </EvokaiProvider>
  )
}

function Assistant() {
  const { session, state } = useEvokaiSession()
  const phase = usePhase() // idle | listening | thinking | speaking | interrupted
  const usage = useUsage() // { dayPct, weekPct, monthPct, companyPct, monthOut, out, binding, resetsAt }
  const voice = useVoice() // start, stop, interrupt, setMuted, interim…
  // Draw messages (state.messages: parts of text, tools, charts, diagrams, questions, confirmations) your way.
  return <MyPanel messages={state.messages} phase={phase} usage={usage} onSend={(text) => session.chat({ text })} onVoice={voice.start} />
}
```

## Plain JavaScript: `/browser`

```ts
import { EvokaiSession, BrowserVoiceEngine, createVoiceEngine } from '@evokai/evokai-client/browser'

const session = new EvokaiSession({
  baseUrl: 'https://agents.example.com/agent',
  locale: 'en', // or 'it': speech rules, voice and recognition language
  getToken: ({ conversationId }) =>
    fetch('/api/agent/token', { method: 'POST', body: JSON.stringify({ conversationId }), headers: { 'Content-Type': 'application/json' } })
      .then((r) => r.json()),
})

// The whole conversation state, for any renderer.
session.subscribe((state) => render(state.messages, state.busy, state.pendingConfirm, state.openAsk, state.phase, state.usage?.dayPct))
// Or raw events.
session.on('navigate', (e) => router.go(e.path))

await session.loadConversation() // the same parts, in the same order, as live (one message per turn); resumes a running turn
const done = await session.chat({ text: 'How many orders shipped this week?' }) // streams; resolves with the `done` event
await session.chat({ text: 'Is this invoice right?', files: [file] }) // File or Blob, when info().attachments allows
const image = await session.attachment(id) // a question's image (ChatMessage.attachments), as a Blob
await session.confirm(actionId, true) // an open confirmation
await session.rewrite('Show last week instead') // replace the last question while `ChatMessage.rewritable`
await session.answer(askId, ['By agent']) // an open question
await session.cancel() // stop the running turn (barge-in, a stop button)
// Your own client tools (docs/client-tools.md): the result of one that waits is posted for you.
session.handleTool('open_cart', async () => ({ opened: await cart.open() }))
// Draw a tool's activity with your own UI (only what the events carry: no server tool output).
session.renderTool('open_cart', (part) => cartBadge.show(part.status))
await session.regenerate() // a new answer to the last question, while ChatMessage.regenerable
await session.newConversation() // asks getToken for a token without conversationId

// Voice: the engine the token chose (`browser` = on-device only, `server` = Pipecat over WebRTC).
const voice = createVoiceEngine(session, { micSound: 'goccia' })
session.on('phase', (e) => console.log(e.phase)) // idle, listening, thinking, speaking, interrupted
button.onclick = () => (voice.state === 'idle' ? voice.start() : voice.stop()) // start from a user gesture
// Any visual: one signal shape for the user's microphone and the agent's voice.
voice.signals.subscribe((s) => orb.update(s.source, s.level, s.onset, s.spectrum))
```

What the session does for you:

- **Streaming**: SSE over `fetch`, parsed across arbitrary chunk boundaries; every event carries `turnId` and `seq`.
- **Citations**: when the agent searches its knowledge bases, `citation` events (`{index, kb, documentId, title,
  location: {page?, section?}, snippet, url}`) arrive before the text that cites them as `[n]`; the store keeps them
  on the answer as `ChatMessage.citations`, also when a stored conversation is reloaded. Pass the user's `groups` when
  minting the token so documents with access groups are found. The admin calls are under `admin.knowledge`
  (`upload`, `addUrl`, `sync`, `search`...), see [knowledge](../../docs/knowledge.md).
- **Replay**: if the stream drops mid-turn, it polls `GET /v1/session/events?after=<last seq>` until the turn's
  `done`; events already applied are skipped.
- **Phases and usage**: `session.on('phase')`, `session.phase`; `session.subscribeUsage(fn)`, percentages only.
- **Tokens**: `getToken` is called at first use, on a 401 (once, then the request is retried), within 30 s of
  `expiresAt`, and on `switchConversation` / `newConversation`.
- **External storage**: `getHistory: () => myHistory` is sent with each turn, `onMessages(messages)` receives the new
  ones.
- **Memory**: `memories()`, `memoryConsent(granted)`, `editMemory(id, text)` and `forgetMemory(id?)`, see
  [Long-term memory](#long-term-memory).

### Voice

**Browser voice** (`BrowserVoiceEngine`) recognises speech on the device only: it requires
`SpeechRecognition.available({ langs, processLocally: true })`, installs the language pack when it is downloadable
(`SpeechRecognition.install`, so call `start()` from a click) and reports `unsupported` otherwise; it never falls back
to a cloud recogniser. It speaks the turn's `say` events in index order (a `say` with a `spoken` form, numbers in words
such as «mille» for «1.000», is spoken in that form, while `segmentStart`, `boundary` and `segmentEnd` carry the shown
`text`, with `boundary.charIndex` mapped onto it; `segment.spoken` is there for timing), sends what the user says as
text with `mode: "voice"` (the server reads "yes", "cancel", "the second" in the session's language), stops speaking
and cancels the turn when the user talks over it, and ignores its own voice picked up by the microphone (in either
form). `Dictation` puts what the user says into your own input instead.

**Speech engines by device** (`chooseTts`): on desktops, for a language with a neural voice (Italian:
`it_IT-serena-high`), the voice runs in the browser with Piper; the model (~114 MB) downloads on first use, is cached,
and reports `progress` and `ready` (show `locales.<code>.voiceText.preparingVoice(persona)` meanwhile) while the
browser voice speaks. On phones, and for other languages, the browser's local voices are used (never a network
voice). Serve the model yourself, by default at `/evokai/voices/<voice>.onnx` and `<voice>.onnx.json` (from the Piper
voices repository), and install the optional runtime:

```sh
npm install onnxruntime-web phonemizer   # optional; phonemizer embeds espeak-ng (GPL-3.0-or-later): see API.md
```

```ts
import { createPiperRuntime } from '@evokai/evokai-client/browser'
const voice = createVoiceEngine(session, {
  ttsOptions: {
    piper: {
      loadRuntime: async () => createPiperRuntime({ ort: await import('onnxruntime-web'), phonemize: (await import('phonemizer')).phonemize }),
    },
  },
})
```

**Audio signals**: the same measurement for every source (RMS × 3.2, syllable onsets, 48 spectrum bins). When the
browser speaks with `speechSynthesis` there is no audio to read, so the signal is estimated from the text with
syllable rules (Italian, and a rough English fallback; other languages give no estimate) and re-aligned on each word
boundary.

**Microphone sounds**: `micSound: 'goccia' | 'classico' | 'vetro' | 'respiro' | 'cristallo' | 'clic' | 'none' |
{ on, off }` (default `goccia`). The identifiers are fixed; English and Italian display names are in the locale packs.
Every built-in sound is synthesised with Web Audio: the package ships no audio files.

**Server voice** (`ServerVoiceEngine`) posts the WebRTC offer to `/v1/session/voice/offer` with the session token
and feeds the RTVI server messages `{ evokai: event }` into the same session state; the call's audio tracks are
measured as the `agent` and `user` signals.

**Locales**: English is the default; Italian is data in `locales.it` (`locale: 'it'` on the session or the engines).
The speech rules are exported (`speakable`, `SentenceSplitter`, `spokenDecision`, `spokenOption`, `isEcho`) and mirror
the server's locale packs, so speech is cut and read the same on both sides.

## Development

```sh
npm ci
npm run gen        # regenerate src/generated/openapi.ts from openapi.json
npm run lint
npm run typecheck
npm test
npm run build      # dist/ (ESM + .d.ts); copies the repository's LICENSE and NOTICE (also on prepack)
```

`openapi.json` is exported from the service; to refresh it, from the repository root:

```sh
uv run evokai openapi --out clients/typescript/openapi.json
cd clients/typescript && npm run gen
```

The generated file is committed and reproducible (same input, same output). The stream events and the chart/schema
shapes are typed by hand in `src/core/types.ts` (the schema describes them as free-form objects): keep them in step
with `src/evokai/sessions/turns.py` and `src/evokai/tools/visuals.py`.

## Licence

Apache-2.0 (see `LICENSE` and `NOTICE`). The optional in-browser voice runtime has its own licences, one of them
GPL-3.0 (espeak-ng inside `phonemizer`): see [API.md](./API.md#6-licences) before shipping it.
