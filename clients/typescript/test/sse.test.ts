import { SseParser } from '../src/browser/sse'

const STREAM =
  ': ping\n\n' +
  'event: text\ndata: {"type":"text","delta":"Hello, déjà vu"}\n\n' +
  'event: chart\r\ndata: {"type":"chart",\r\ndata: "x":1}\r\n\r\n' +
  'event: done\rdata: {"type":"done"}\r\r'

function parseAll(chunks: Array<string | Uint8Array>) {
  const p = new SseParser()
  return [...chunks.flatMap((c) => p.push(c)), ...p.end()]
}

const expected = [
  { event: 'text', data: '{"type":"text","delta":"Hello, déjà vu"}' },
  { event: 'chart', data: '{"type":"chart",\n"x":1}' },
  { event: 'done', data: '{"type":"done"}' },
]

describe('SseParser', () => {
  test('parses a whole stream: comments, LF, CRLF, CR, multi-line data', () => {
    expect(parseAll([STREAM])).toEqual(expected)
  })

  test('the same messages wherever the text is split', () => {
    for (let i = 1; i < STREAM.length; i++) {
      expect(parseAll([STREAM.slice(0, i), STREAM.slice(i)])).toEqual(expected)
    }
  })

  test('the same messages wherever the bytes are split, even inside a multi-byte character', () => {
    const bytes = new TextEncoder().encode(STREAM)
    for (let i = 1; i < bytes.length; i++) {
      expect(parseAll([bytes.slice(0, i), bytes.slice(i)])).toEqual(expected)
    }
  })

  test('one byte at a time', () => {
    const bytes = new TextEncoder().encode(STREAM)
    expect(parseAll(Array.from(bytes, (b) => new Uint8Array([b])))).toEqual(expected)
  })

  test('fields: no space after the colon, id and retry; an unterminated message is dropped', () => {
    const p = new SseParser()
    expect(p.push('id: 7\nretry: 3000\nevent:x\ndata:1\n\ndata: dangling')).toEqual([{ event: 'x', data: '1', id: '7', retry: 3000 }])
    expect(p.end()).toEqual([])
  })

  test('a message without event is "message"', () => {
    expect(parseAll(['data: a\n\n'])).toEqual([{ event: 'message', data: 'a' }])
  })
})
