/**
 * A server-sent events parser that does not care where the network cut the bytes: chunks may split a line, a field,
 * a CRLF pair or a multi-byte character. Follows the WHATWG event-stream rules (comments, `event:`, multi-line
 * `data:`, one optional space after the colon, CR / LF / CRLF line ends).
 */

export interface SseMessage {
  /** The `event:` field, or `message` when absent. */
  event: string
  data: string
  id?: string
  retry?: number
}

export class SseParser {
  private buf = ''
  private data: string[] = []
  private event = ''
  private id: string | undefined
  private retry: number | undefined
  private sawCR = false
  private readonly decoder = new TextDecoder('utf-8')

  /** Feed bytes (or already decoded text); returns the messages completed so far. */
  push(chunk: Uint8Array | string): SseMessage[] {
    const text = typeof chunk === 'string' ? chunk : this.decoder.decode(chunk, { stream: true })
    return this.consume(text)
  }

  /** End of stream: a trailing message without its blank line is dropped, as the spec says. */
  end(): SseMessage[] {
    const out = this.consume(this.decoder.decode())
    this.buf = ''
    this.data = []
    this.event = ''
    return out
  }

  private consume(text: string): SseMessage[] {
    const out: SseMessage[] = []
    let s = text
    if (s === '') return out
    // A CR at the end of the previous chunk already ended its line: skip the LF of that CRLF.
    if (this.sawCR && s.startsWith('\n')) s = s.slice(1)
    this.sawCR = false
    this.buf += s
    let start = 0
    for (let i = 0; i < this.buf.length; i++) {
      const ch = this.buf[i]
      if (ch !== '\n' && ch !== '\r') continue
      const line = this.buf.slice(start, i)
      if (ch === '\r') {
        if (i + 1 < this.buf.length) {
          if (this.buf[i + 1] === '\n') i++
        } else {
          this.sawCR = true
        }
      }
      start = i + 1
      const msg = this.line(line)
      if (msg) out.push(msg)
    }
    this.buf = this.buf.slice(start)
    return out
  }

  private line(line: string): SseMessage | null {
    if (line === '') {
      if (!this.data.length) {
        this.event = ''
        return null
      }
      const msg: SseMessage = { event: this.event || 'message', data: this.data.join('\n') }
      if (this.id !== undefined) msg.id = this.id
      if (this.retry !== undefined) msg.retry = this.retry
      this.data = []
      this.event = ''
      return msg
    }
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') this.data.push(value)
    else if (field === 'event') this.event = value
    else if (field === 'id' && !value.includes('\0')) this.id = value
    else if (field === 'retry' && /^\d+$/.test(value)) this.retry = Number(value)
    return null
  }
}

/** Read a fetch body as SSE messages. */
export async function* readSse(body: ReadableStream<Uint8Array>, signal?: AbortSignal): AsyncGenerator<SseMessage> {
  const parser = new SseParser()
  const reader = body.getReader()
  try {
    for (;;) {
      if (signal?.aborted) return
      const { value, done } = await reader.read()
      if (done) break
      for (const m of parser.push(value)) yield m
    }
    for (const m of parser.end()) yield m
  } finally {
    try {
      reader.releaseLock()
    } catch {
      /* already released */
    }
  }
}
