import { unlockAudio } from '../src/browser/audio/unlock'

describe('unlockAudio', () => {
  test('from the gesture: a clean queue, a silent utterance, the shared context resumed with one silent sample', () => {
    const calls: string[] = []
    class Utt {
      volume = 1
      constructor(readonly text: string) {}
    }
    const synthesis = {
      cancel: () => calls.push('cancel'),
      resume: () => calls.push('resume'),
      speak: (u: unknown) => calls.push(`speak:${(u as Utt).text}:${(u as Utt).volume}`),
    }
    const context = {
      state: 'suspended',
      resume: () => {
        calls.push('ctx.resume')
        return Promise.resolve()
      },
      destination: {},
      createBuffer: () => ({}),
      createBufferSource: () => ({ buffer: null as unknown, connect: () => calls.push('connect'), start: () => calls.push('start') }),
    }
    unlockAudio({ synthesis, Utterance: Utt, context })
    expect(calls).toEqual(['cancel', 'resume', 'speak: :0', 'ctx.resume', 'connect', 'start'])
  })

  test('without speech synthesis or Web Audio it does nothing and never throws', () => {
    expect(() => unlockAudio({ synthesis: null, context: null })).not.toThrow()
    const broken = { cancel: () => { throw new Error('x') }, speak: () => undefined }
    expect(() => unlockAudio({ synthesis: broken, Utterance: class { volume = 1 }, context: null })).not.toThrow()
  })
})
