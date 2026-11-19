// The components against a scripted Evokai inside the page (no backend): a token, the session routes and one turn
// with an answer and a chart. A fake on-device recogniser lets voice mode start; `window.evokaiFake` sets the voice
// phase, the subtitles and the levels by hand, for screenshots (e2e/voice-panel.mjs).
import { defineElements } from '../src/index'

defineElements()

class FakeRecognition {
  static available = async () => 'available'
  static install = async () => true
  lang = ''
  continuous = false
  interimResults = false
  onresult = null
  onend = null
  onerror = null
  onstart = null
  start() {}
  stop() {}
  abort() {}
  addEventListener() {}
  removeEventListener() {}
}
Object.assign(window, { SpeechRecognition: FakeRecognition, webkitSpeechRecognition: FakeRecognition })

const usage = { dayPct: 12.5, monthPct: 4, companyPct: 2, monthOut: false }
const CHART = {
  type: 'bar',
  title: 'Returns by reason',
  subtitle: 'This week',
  unit: 'count',
  points: [
    { label: 'Damaged in transit', value: 12 },
    { label: 'Wrong title', value: 8 },
    { label: 'Changed mind', value: 6 },
  ],
  highlight: 'Damaged in transit',
  name: '',
  compare: null,
}
let seq = 0
const sse = (events: Array<Record<string, unknown>>) => events.map((e) => `event: ${e.type}\ndata: ${JSON.stringify({ turnId: 'tt', seq: ++seq, ...e })}\n\n`).join('')
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } })
const fetcher = async (input: RequestInfo | URL): Promise<Response> => {
  const url = String(input)
  if (url.endsWith('/token')) return json({ token: 'tok', conversationId: 'c1', expiresAt: Date.now() / 1000 + 600, usage, suggestions: ['Which reason has the most returns?', 'What ships this week?'] })
  if (url.endsWith('/v1/session')) return json({ conversationId: 'c1', agent: 'helper', expiresAt: Date.now() / 1000 + 600, usage, memories: [], memory: { mode: 'off', consent: 'unknown', active: false, ask: false } })
  if (url.endsWith('/v1/session/conversation')) return json({ messages: [], pending: null, lastSeq: 0, runningTurnId: null })
  // `?scene=confirm`: the answer waits on a call an ask rule asked for (the reason line), and the question can be edited.
  if (url.endsWith('/v1/session/chat') && new URLSearchParams(location.search).get('scene') === 'confirm')
    return new Response(
      sse([
        { type: 'conversation', id: 'c1', turnStarted: true },
        { type: 'text', delta: 'I can refund order 1042.' },
        {
          type: 'confirm',
          actionId: 'a1',
          items: ['Refund order 1042 (The Lighthouse Keeper)'],
          why: [{ key: 'confirm.why.rule', rule: 'refunds', level: 'application', label: chat.getAttribute('locale') === 'it' ? 'Rimborsi: chiedi prima di rimborsare un ordine' : 'Refunds: ask before refunding an order' }],
        },
        { type: 'done', conversationId: 'c1', reason: 'confirm', rewritable: true },
      ]),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )
  if (url.endsWith('/v1/session/chat'))
    return new Response(
      sse([
        { type: 'conversation', id: 'c1', turnStarted: true },
        { type: 'text', delta: 'Damaged in transit leads with **12 returns**, the most of the three reasons. I highlighted it in the chart.' },
        { type: 'chart', chart: CHART },
        { type: 'done', conversationId: 'c1', reason: 'end_turn' },
      ]),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )
  return json({})
}

const chat = document.getElementById('chat') as HTMLElementTagNameMap['evokai-chat']
chat.fetcher = fetcher
const params = new URLSearchParams(location.search)
if (params.get('locale')) chat.setAttribute('locale', params.get('locale') as string)
if (params.get('scheme')) chat.setAttribute('scheme', params.get('scheme') as string)

type Ctl = { state: { session: Record<string, unknown> }; voice: { signals: { current: (s: string) => unknown } } | null; patch(p: Record<string, unknown>): void }
const ctl = () => chat.controller as unknown as Ctl
const levels = { user: 0, agent: 0 }
let wobble = 0
/** Set the voice state by hand: the phase, what was heard, the agent's line and how far it got, the levels. */
function voice(o: { phase?: string; heard?: string; line?: string; upto?: number; user?: number; agent?: number; noVoice?: boolean }) {
  const c = ctl()
  const sig = c.voice?.signals
  if (sig && !(sig as { faked?: boolean }).faked) {
    Object.assign(sig, {
      faked: true,
      current: (src: string) => {
        const l = src === 'user' ? levels.user : levels.agent * (0.6 + 0.4 * Math.abs(Math.sin((wobble += 0.13))))
        return { source: src, rms: l, level: l, onset: src === 'agent' && levels.agent > 0 && Math.random() < 0.15, intensity: 0.6, spectrum: null, estimated: true, time: performance.now() }
      },
    })
  }
  if (o.user != null) levels.user = o.user
  if (o.agent != null) levels.agent = o.agent
  c.patch({
    session: { ...c.state.session, ...(o.phase ? { phase: o.phase } : {}) },
    ...(o.heard != null ? { heard: o.heard } : {}),
    ...(o.line != null ? { line: { text: o.line, upto: o.upto ?? 0 } } : {}),
    noVoice: o.noVoice ?? false,
  })
}
Object.assign(window, { evokaiFake: { voice, chat } })
