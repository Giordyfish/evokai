import { CLASSICO_URLS, MIC_SOUND_RECIPES, playMicSound, type AudioContextLike } from '../src/browser/audio/mic-sounds'
import { FakeAudioContext, type FakeFilter, type FakeGain, type FakeOsc, type FakeSource } from './helpers/audio'

const play = async (sound: Parameters<typeof playMicSound>[0], which: 'on' | 'off', extra: Record<string, unknown> = {}) => {
  const ctx = new FakeAudioContext()
  await playMicSound(sound, which, { context: ctx as unknown as AudioContextLike, ...extra })
  return ctx
}
const t0 = 10 // the fake context's currentTime
const outGain = (ctx: FakeAudioContext) => ctx.of<FakeGain>('gain').find((g) => g.connections.includes(ctx.destination))!.gain.value

describe('microphone sounds', () => {
  test('goccia: 988 Hz then 1319 Hz to open, reversed to close; 8 ms attack, exponential decay, gain .18', async () => {
    const on = await play('goccia', 'on')
    const oscs = on.of<FakeOsc>('osc')
    expect(oscs.map((o) => o.frequency.calls[0]!.value)).toEqual([988, 1319])
    expect(oscs.map((o) => o.started[0]! - t0)).toEqual([0, 0.08].map((x) => expect.closeTo(x, 6)) as never)
    expect(oscs.map((o) => +(o.stopped[0]! - t0).toFixed(3))).toEqual([0.19, 0.35])
    const env = on.of<FakeGain>('gain').filter((g) => !g.connections.includes(on.destination))[0]!.gain.calls
    expect(env.map((c) => [c.fn, c.value, +(c.at - t0).toFixed(3)])).toEqual([
      ['set', 0, 0],
      ['linear', 1, 0.008],
      ['exp', 0.0008, 0.14],
    ])
    expect(outGain(on)).toBeCloseTo(0.18)
    const off = await play('goccia', 'off')
    expect(off.of<FakeOsc>('osc').map((o) => o.frequency.calls[0]!.value)).toEqual([1319, 988])
  })

  test('vetro: an FM bell, modulator × 3.5 with a decaying index 2.2, 1568 Hz on / 1046 Hz off, gain .12', async () => {
    const on = await play('vetro', 'on')
    const [carrier, mod] = on.of<FakeOsc>('osc')
    expect(carrier!.frequency.value).toBe(1568)
    expect(mod!.frequency.value).toBeCloseTo(1568 * 3.5)
    const modGain = on.of<FakeGain>('gain').find((g) => g.connections.includes(carrier!.frequency))!
    expect(modGain.gain.calls.map((c) => [c.fn, c.value])).toEqual([
      ['set', 1568 * 2.2],
      ['exp', 1],
    ])
    expect(+(modGain.gain.calls[1]!.at - t0).toFixed(3)).toBe(0.6)
    expect(outGain(on)).toBeCloseTo(0.12)
    expect((await play('vetro', 'off')).of<FakeOsc>('osc')[0]!.frequency.value).toBe(1046)
  })

  test('respiro: Q6 band-pass noise sweeping 500 → 2600 Hz over .35 s (reversed to close), gain .5', async () => {
    const on = await play('respiro', 'on')
    const f = on.of<FakeFilter>('filter')[0]!
    expect(f.type).toBe('bandpass')
    expect(f.Q.value).toBe(6)
    expect(f.frequency.calls.map((c) => [c.fn, c.value, +(c.at - t0).toFixed(3)])).toEqual([
      ['set', 500, 0],
      ['exp', 2600, 0.35],
    ])
    expect(outGain(on)).toBeCloseTo(0.5)
    const off = await play('respiro', 'off')
    expect(off.of<FakeFilter>('filter')[0]!.frequency.calls.map((c) => c.value)).toEqual([2600, 500])
  })

  test('cristallo: a triangle arpeggio 880/1109/1319 Hz, 45 ms apart, descending to close, gain .11', async () => {
    const on = await play('cristallo', 'on')
    const oscs = on.of<FakeOsc>('osc')
    expect(oscs.map((o) => [o.type, o.frequency.calls[0]!.value, +(o.started[0]! - t0).toFixed(3)])).toEqual([
      ['triangle', 880, 0],
      ['triangle', 1109, 0.045],
      ['triangle', 1319, 0.09],
    ])
    expect(outGain(on)).toBeCloseTo(0.11)
    expect((await play('cristallo', 'off')).of<FakeOsc>('osc').map((o) => o.frequency.calls[0]!.value)).toEqual([1319, 1109, 880])
  })

  test('clic: a 0.09 s sine glide 600 → 1200 Hz, reversed to close, gain .22', async () => {
    const on = await play('clic', 'on')
    const f = on.of<FakeOsc>('osc')[0]!.frequency.calls
    expect(f.map((c) => [c.fn, c.value])).toEqual([
      ['set', 600],
      ['exp', 1200],
    ])
    expect(+(f[1]!.at - t0).toFixed(4)).toBe(0.063)
    expect(outGain(on)).toBeCloseTo(0.22)
    expect((await play('clic', 'off')).of<FakeOsc>('osc')[0]!.frequency.calls.map((c) => c.value)).toEqual([1200, 600])
  })

  test('classico plays the packaged files; {on, off} plays yours; none plays nothing; volume scales', async () => {
    const urls: string[] = []
    const fetchFn = async (u: string) => {
      urls.push(u)
      return new Response(new Uint8Array([1, 2, 3]))
    }
    const c1 = await play('classico', 'on', { fetch: fetchFn })
    expect(urls[0]).toMatch(/\/sounds\/classico-on\.mp3$/)
    expect(CLASSICO_URLS.off()).toMatch(/\/sounds\/classico-off\.mp3$/)
    expect(c1.of<FakeSource>('source')[0]!.started).toHaveLength(1)
    await play({ on: 'https://app.test/on.ogg', off: 'https://app.test/off.ogg' }, 'off', { fetch: fetchFn })
    expect(urls[1]).toBe('https://app.test/off.ogg')
    expect((await play('none', 'on')).nodes).toHaveLength(0)
    expect(outGain(await play('goccia', 'on', { volume: 0.5 }))).toBeCloseTo(0.09)
    expect(Object.keys(MIC_SOUND_RECIPES).sort()).toEqual(['clic', 'cristallo', 'goccia', 'respiro', 'vetro'])
  })

  test('a suspended context is resumed first', async () => {
    const ctx = new FakeAudioContext()
    ctx.state = 'suspended'
    await playMicSound('goccia', 'on', { context: ctx as unknown as AudioContextLike })
    expect(ctx.state).toBe('running')
  })
})
