import { ingestAll, setup } from './helpers/voice'

const SHOWN = 'Sono 1.000 punti, il 12,5% in più.'
const SPOKEN = 'Sono mille punti, il dodici virgola cinque per cento in più.'

function numbers(s: ReturnType<typeof setup>) {
  ingestAll(s.session, [
    { type: 'conversation', id: 'c', turnStarted: true, turnId: 't1', seq: 1 },
    { type: 'say', index: 0, text: SHOWN, spoken: SPOKEN, turnId: 't1', seq: 2 },
    { type: 'say', index: 1, text: 'Altro?', turnId: 't1', seq: 3 },
  ])
}

afterEach(() => {
  vi.useRealTimers()
})

describe('numbers in words: spoken by the voice, shown as digits', () => {
  test('the voice says the spoken form; every event to the page carries the shown text', async () => {
    const s = setup()
    await s.engine.start()
    const starts: Array<{ text: string; spoken?: string }> = []
    const ends: string[] = []
    s.engine.on('segmentStart', (seg) => starts.push({ text: seg.text, spoken: seg.spoken }))
    s.engine.on('segmentEnd', (seg) => ends.push(seg.text))
    numbers(s)
    await vi.waitFor(() => expect(s.tts.spoken).toEqual([SPOKEN]))
    s.tts.synth.finish()
    await vi.waitFor(() => expect(s.tts.spoken).toEqual([SPOKEN, 'Altro?']))
    s.tts.synth.finish()
    await vi.waitFor(() => expect(ends).toEqual([SHOWN, 'Altro?']))
    expect(starts).toEqual([{ text: SHOWN, spoken: SPOKEN }, { text: 'Altro?', spoken: undefined }])
  })

  test('a word boundary in the spoken form is mapped onto the shown text, clamped and never going back', async () => {
    const s = setup()
    await s.engine.start()
    const marks: Array<{ text: string; charIndex: number }> = []
    s.engine.on('boundary', ({ segment, charIndex }) => marks.push({ text: segment.text, charIndex }))
    numbers(s)
    await vi.waitFor(() => expect(s.tts.utterances).toHaveLength(1))
    const u = s.tts.utterances[0]!
    const at = (word: string) => SPOKEN.indexOf(word)
    u.onboundary?.({ name: 'word', charIndex: at('punti') })
    u.onboundary?.({ name: 'word', charIndex: at('dodici') })
    u.onboundary?.({ name: 'word', charIndex: at('mille') }) // reported late: the subtitle does not go back
    u.onboundary?.({ name: 'word', charIndex: SPOKEN.length + 10 })
    expect(marks.every((m) => m.text === SHOWN)).toBe(true)
    const idx = marks.map((m) => m.charIndex)
    expect(idx[0]).toBe(Math.round((at('punti') * SHOWN.length) / SPOKEN.length))
    expect(idx[1]).toBeGreaterThan(idx[0]!)
    expect(idx[2]).toBe(idx[1])
    expect(idx[3]).toBe(SHOWN.length)
    expect(idx.every((i) => i >= 0 && i <= SHOWN.length)).toBe(true)
  })

  test('the echo guard knows both forms: the agent heard back in words or in digits is not the user', async () => {
    const s = setup({ engine: { settleMs: 0 } })
    await s.engine.start()
    numbers(s)
    await vi.waitFor(() => expect(s.tts.spoken).toEqual([SPOKEN]))
    const r = s.rec.instances[0]!
    r.say('sono mille punti il dodici virgola cinque per cento', true)
    r.say('sono 1.000 punti il 12,5% in più', true)
    expect(s.engine.held).toBe(false)
    expect(s.m.calls.filter((c) => c.url.endsWith('/chat'))).toHaveLength(0)
    expect(s.tts.spoken).toEqual([SPOKEN])
  })

  test('hold and resume with a spoken form: the rest is said from the word reached, no sentence is lost', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { holdMs: 1000, settleMs: 0 } })
    await s.engine.start()
    const starts: string[] = []
    s.engine.on('segmentStart', (seg) => starts.push(seg.text))
    numbers(s)
    expect(s.tts.spoken).toEqual([SPOKEN])
    vi.advanceTimersByTime(1500)
    s.rec.instances[0]!.say('aspetta', false)
    expect(s.engine.held).toBe(true)
    vi.advanceTimersByTime(1100)
    expect(s.engine.held).toBe(false)
    const resumed = s.tts.spoken.slice(1)
    expect(resumed[0]).not.toBe(SPOKEN)
    expect(SPOKEN.endsWith(resumed[0]!)).toBe(true)
    // A line cut mid-way shows the words it says.
    expect(starts.at(-1)).toBe(resumed[0])
    s.tts.synth.finish()
    expect(s.tts.spoken.at(-1)).toBe('Altro?')
    expect(s.m.calls).toHaveLength(0)
  })

  test('held before its first word was said, the whole line resumes with its shown text', async () => {
    vi.useFakeTimers()
    const s = setup({ engine: { holdMs: 1000, settleMs: 0 } })
    await s.engine.start()
    const starts: string[] = []
    s.engine.on('segmentStart', (seg) => starts.push(seg.text))
    numbers(s)
    s.rec.instances[0]!.say('aspetta', false)
    expect(s.engine.held).toBe(true)
    vi.advanceTimersByTime(1100)
    expect(s.tts.spoken).toEqual([SPOKEN, SPOKEN])
    expect(starts).toEqual([SHOWN, SHOWN])
  })
})
