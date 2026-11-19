import { interruptsOf, runAgui, type AguiEvent } from '../src/browser/agui'
import { EvokaiError } from '../src/core/http'

const frames = [
  { type: 'RUN_STARTED', threadId: 't', runId: 'r' },
  { type: 'TEXT_MESSAGE_START', messageId: 'm', role: 'assistant' },
  { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm', delta: 'Ciao' },
  { type: 'TEXT_MESSAGE_END', messageId: 'm' },
  {
    type: 'RUN_FINISHED',
    threadId: 't',
    runId: 'r',
    outcome: { type: 'interrupt', interrupts: [{ id: 'a1', reason: 'confirmation', message: 'Delete?' }] },
  },
]

function sse(events: unknown[]): Response {
  const body = ': ping\n\n' + events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('runAgui', () => {
  test('posts the input with the token and yields typed events until the run ends', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetch = async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      return sse([...frames, { type: 'CUSTOM', name: 'after', value: 1 }])
    }
    const input = { threadId: 't', runId: 'r', messages: [{ id: '1', role: 'user' as const, content: 'Ciao' }] }
    const out: AguiEvent[] = []
    for await (const e of runAgui({ baseUrl: 'https://x.test/evokai/', token: 'tok', input, fetch })) out.push(e)
    expect(calls[0]?.url).toBe('https://x.test/evokai/v1/session/agui')
    expect((calls[0]?.init?.headers as Record<string, string>).Authorization).toBe('Bearer tok')
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual(input)
    expect(out.map((e) => e.type)).toEqual(frames.map((e) => e.type))
    expect(interruptsOf(out[out.length - 1]!)).toEqual([{ id: 'a1', reason: 'confirmation', message: 'Delete?' }])
    expect(interruptsOf(out[0]!)).toEqual([])
  })

  test('a refused run throws the service error', async () => {
    const fetch = async () =>
      new Response(JSON.stringify({ error: 'origin', message: 'Origin not allowed.' }), { status: 403 })
    const input = { threadId: 't', runId: 'r', messages: [] }
    const run = runAgui({ baseUrl: 'https://x.test', token: 'tok', input, fetch })
    await expect(run.next()).rejects.toBeInstanceOf(EvokaiError)
  })
})
