import { EvokaiSession } from '../src/browser/session'
import { fromHistory, fromStored } from '../src/browser/store'
import type { EvokaiEvent } from '../src/core/types'
import { frame, json, mockFetch, sse } from './helpers/fetch'

const BASE = 'https://agents.test/agent'
const done: EvokaiEvent[] = [{ type: 'done', conversationId: 'c1', reason: 'end_turn', turnId: 't1', seq: 1 }]

describe('attachments', () => {
  test('chat sends files as base64 and shows them on the question', async () => {
    const m = mockFetch({ 'POST /agent/v1/session/chat': () => sse(done.map(frame), { turnId: 't1' }) })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    const photo = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'me.png', { type: 'image/png' })
    const note = new Blob(['hello'], { type: 'text/plain' })
    await s.chat({ text: 'Look', files: [photo, note] })
    expect(m.calls[0]!.body).toEqual({
      mode: 'text',
      text: 'Look',
      attachments: [
        { name: 'me.png', mediaType: 'image/png', data: 'iVBORw==' },
        { name: 'file', mediaType: 'text/plain', data: 'aGVsbG8=' },
      ],
    })
    const question = s.state.messages[0]!
    expect(question.attachments?.map((a) => [a.name, a.mediaType, a.size])).toEqual([
      ['me.png', 'image/png', 4],
      ['file', 'text/plain', 5],
    ])
    expect(question.attachments?.[0]?.url).toMatch(/^blob:/)
    expect(question.attachments?.[1]?.url).toBeUndefined()
  })

  test('the local preview of an image is revoked once no message shows it, not before', async () => {
    const revoked: string[] = []
    const spy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation((u: string) => void revoked.push(u))
    try {
      const m = mockFetch({ 'POST /agent/v1/session/chat': () => sse(done.map(frame), { turnId: 't1' }) })
      const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
      const photo = new File([new Uint8Array([0x89, 0x50])], 'me.png', { type: 'image/png' })
      await s.chat({ text: 'Look', files: [photo] })
      const url = s.state.messages[0]!.attachments![0]!.url!
      expect(url).toMatch(/^blob:/)
      // Still shown after the turn, and after other changes of the conversation.
      s.store.dispatch({ type: 'usage', usage: { dayPct: 1, monthPct: 1, companyPct: 1, monthOut: false } })
      expect(revoked).toEqual([])
      // The conversation is reloaded from the service (its copy has ids, no local URLs): revoked.
      s.store.dispatch({ type: 'loaded', conversation: { messages: [], pending: null, lastSeq: 5, runningTurnId: null } })
      expect(revoked).toEqual([url])
      // A new conversation drops the rest.
      await s.chat({ text: 'Again', files: [photo] })
      const next = s.state.messages.find((x) => x.role === 'user')!.attachments![0]!.url!
      s.store.dispatch({ type: 'reset' })
      expect(revoked).toEqual([url, next])
    } finally {
      spy.mockRestore()
    }
  })

  test('the history carries the attachments of a question, and an image is fetched with the token', async () => {
    const msgs = fromStored(
      [
        {
          id: 'm1',
          role: 'user',
          text: 'Look',
          events: [],
          feedback: null,
          attachments: [{ id: 'a1', name: 'me.png', mediaType: 'image/png', size: 4 }],
        },
      ],
      null,
    )
    expect(msgs[0]!.attachments).toEqual([{ id: 'a1', name: 'me.png', mediaType: 'image/png', size: 4 }])
    const external = fromHistory([
      {
        role: 'user',
        text: 'Look',
        content: [{ file: { id: 'a2', name: 'cv.docx', mediaType: 'application/x', size: 9, sha256: '' }, text: 'x' }, { text: 'Look' }],
        events: [],
      },
    ])
    expect(external[0]!.attachments).toEqual([{ id: 'a2', name: 'cv.docx', mediaType: 'application/x', size: 9 }])

    const m = mockFetch({
      'GET /agent/v1/session/attachments/a1': () => new Response(new Uint8Array([1, 2]), { headers: { 'Content-Type': 'image/png' } }),
      'GET /agent/v1/session/attachments/zz': () => json({ error: 'unknown_attachment', message: 'Unknown attachment.' }, 404),
    })
    const s = new EvokaiSession({ baseUrl: BASE, token: 'tok', fetch: m.fetch })
    const blob = await s.attachment('a1')
    expect(blob.size).toBe(2)
    expect(m.calls[0]!.headers.authorization).toBe('Bearer tok')
    await expect(s.attachment('zz')).rejects.toMatchObject({ status: 404, code: 'unknown_attachment' })
  })
})
