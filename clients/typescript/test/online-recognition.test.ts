import { OnDeviceStt, recognitionIsLocal, recognitionSupport, type SpeechRecognitionLike, type SpeechRecognitionStatic } from '../src/browser/stt/on-device'

function recogniser(available?: 'available' | 'unavailable') {
  const made: SpeechRecognitionLike[] = []
  const C = class {
    lang = ''
    continuous = false
    interimResults = false
    processLocally?: boolean
    onresult = null
    onerror = null
    onend = null
    started: unknown[] = []
    constructor() {
      made.push(this as unknown as SpeechRecognitionLike)
    }
    start(...a: unknown[]) {
      this.started = a
    }
    stop() {}
    abort() {}
  } as unknown as SpeechRecognitionStatic
  if (available) C.available = async () => available
  return { C, made }
}

describe('online recognition where there is no on-device one', () => {
  test('iOS (a recogniser without on-device support): off by default, ready when the application allows it', async () => {
    const { C } = recogniser()
    expect(await recognitionSupport({ recognition: C })).toBe('unsupported')
    expect(await recognitionSupport({ recognition: C, allowOnline: true })).toBe('ready')
    expect(await recognitionIsLocal({ recognition: C })).toBe(false)
  })

  test('on-device first: where it exists it is used even with online allowed', async () => {
    const { C, made } = recogniser('available')
    expect(await recognitionIsLocal({ recognition: C })).toBe(true)
    const stt = new OnDeviceStt({ recognition: C, allowOnline: true })
    await stt.start({ continuous: false })
    expect(made[0]!.processLocally).toBe(true)
  })

  test('the online recogniser is not asked to process locally and captures the microphone itself', async () => {
    const { C, made } = recogniser()
    const stt = new OnDeviceStt({ recognition: C, allowOnline: true })
    await stt.start({ continuous: true, track: {} as MediaStreamTrack })
    expect(made[0]!.processLocally).toBeUndefined()
    expect((made[0] as unknown as { started: unknown[] }).started).toEqual([])
  })

  test('without the permission a device with no on-device recogniser still refuses to start', async () => {
    const { C } = recogniser('unavailable')
    const stt = new OnDeviceStt({ recognition: C })
    await expect(stt.start({ continuous: false })).rejects.toThrow('unsupported')
  })
})
