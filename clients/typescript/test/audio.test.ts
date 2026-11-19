import { AudioSignals, measure, ONSET_JUMP, SPECTRUM_BINS, type AnalyserLike } from '../src/browser/audio/signals'
import { Microphone } from '../src/browser/mic/microphone'

function analyser(samples: Float32Array, bins: Uint8Array): AnalyserLike {
  return {
    fftSize: samples.length,
    getFloatTimeDomainData: (a) => a.set(samples),
    getByteFrequencyData: (a) => a.set(bins.subarray(0, a.length)),
  }
}

describe('measure', () => {
  test('RMS × 3.2 on a sine, level and spectrum bins', () => {
    const n = 1024
    const sine = Float32Array.from({ length: n }, (_, i) => 0.1 * Math.sin((2 * Math.PI * 8 * i) / n))
    const bins = Uint8Array.from({ length: n / 2 }, (_, i) => (i === 2 ? 255 : i === 3 ? 51 : 0))
    const m = measure(analyser(sine, bins), 0)
    expect(m.rms).toBeCloseTo((0.1 / Math.SQRT2) * 3.2, 3)
    expect(m.level).toBeCloseTo(0.1 + m.rms * 0.85, 5)
    expect(m.onset).toBe(true)
    expect(m.spectrum).toHaveLength(SPECTRUM_BINS)
    expect(m.spectrum[0]).toBe(1)
    expect(m.spectrum[1]).toBeCloseTo(0.2, 5)
  })

  test('an onset needs a jump above the threshold; loud input clamps to 1', () => {
    const quiet = new Float32Array(256).fill(0.02)
    const r = measure(analyser(quiet, new Uint8Array(128)), 0.064 - ONSET_JUMP / 2).onset
    expect(r).toBe(false)
    expect(measure(analyser(new Float32Array(256).fill(0.9), new Uint8Array(128)), 0).rms).toBe(1)
  })

  test('AudioSignals keeps the latest per source and settles on silence', () => {
    const s = new AudioSignals()
    const got: number[] = []
    s.subscribe((x) => got.push(x.rms))
    s.push({ source: 'user', rms: 0.5, level: 0.5, onset: true, intensity: 0.8, spectrum: null, estimated: false, time: 1 })
    s.silence('user')
    expect(got).toEqual([0.5, 0])
    expect(s.current('user')?.rms).toBe(0)
    expect(s.current('agent')).toBeNull()
  })
})

function fakeDevices(opts: { deny?: boolean } = {}) {
  const listeners: Array<() => void> = []
  let inputs = [
    { kind: 'audioinput', deviceId: 'default', label: 'Default', groupId: 'g' },
    { kind: 'audioinput', deviceId: 'usb', label: 'USB mic', groupId: 'u' },
    { kind: 'audiooutput', deviceId: 'spk', label: 'Speakers', groupId: 'g' },
  ]
  const tracks: Array<{ enabled: boolean; stopped: boolean }> = []
  const md = {
    getUserMedia: vi.fn(async () => {
      if (opts.deny) throw Object.assign(new Error('denied'), { name: 'NotAllowedError' })
      const track = { enabled: true, stopped: false, stop() { this.stopped = true }, addEventListener() {} }
      tracks.push(track)
      return { getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream
    }),
    enumerateDevices: async () => inputs as MediaDeviceInfo[],
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    removeEventListener: () => undefined,
  }
  return {
    md: md as unknown as MediaDevices,
    tracks,
    unplug: (id: string) => {
      inputs = inputs.filter((d) => d.deviceId !== id)
      listeners.forEach((f) => f())
    },
  }
}

describe('Microphone', () => {
  test('capture, mute, devices and selection', async () => {
    const f = fakeDevices()
    const mic = new Microphone({ mediaDevices: f.md, permissions: null })
    await mic.start()
    expect(mic.state.active).toBe(true)
    expect(mic.state.permission).toBe('granted')
    await vi.waitFor(() => expect(mic.state.devices.map((d) => d.deviceId)).toEqual(['default', 'usb']))
    mic.mute()
    expect(f.tracks[0]!.enabled).toBe(false)
    expect(mic.state.muted).toBe(true)
    await mic.select('usb')
    expect(f.tracks[0]!.stopped).toBe(true)
    expect(f.tracks[1]!.enabled).toBe(false)
    expect(mic.state.deviceId).toBe('usb')
    mic.stop()
    expect(mic.state.active).toBe(false)
  })

  test('an unplugged chosen device falls back to the default and is reported', async () => {
    const f = fakeDevices()
    const mic = new Microphone({ mediaDevices: f.md, permissions: null, deviceId: 'usb' })
    await mic.start()
    const lost: string[] = []
    const changes: number[] = []
    mic.on('devicelost', (d) => lost.push(d.deviceId))
    mic.on('devicechange', (d) => changes.push(d.length))
    f.unplug('usb')
    await vi.waitFor(() => expect(lost).toEqual(['usb']))
    expect(changes).toEqual([1])
    await vi.waitFor(() => expect(mic.state.deviceId).toBeNull())
  })

  test('a refused permission is reported', async () => {
    const mic = new Microphone({ mediaDevices: fakeDevices({ deny: true }).md, permissions: null })
    const errors: string[] = []
    mic.on('error', (e) => errors.push(e.code))
    await expect(mic.start()).rejects.toThrow()
    expect(mic.state.permission).toBe('denied')
    expect(errors).toEqual(['not-allowed'])
    expect(new Microphone({ mediaDevices: null }).state.permission).toBe('unsupported')
  })
})
