import { setLocale } from '../src/i18n'
import { type AssistantPrivacy, needsNotice, noticeLines, viewedLine, voiceKind, voiceOptions, voiceText } from '../src/assistant/privacy'

afterEach(() => setLocale('en'))

const base: AssistantPrivacy = { noticeVersion: 1, accepted: true, acceptedAt: '2026-11-20T10:00:00Z', ttlDays: 30, voice: 'device', inspections: [], unseen: 0 }
const inspection = { conversationId: 'c1', by: 'olga@example.com', at: '2026-11-21T09:30:00Z', reason: 'Checking a change to the caps reported by finance' }

describe('the first-use notice', () => {
  it('opens before the first chat, after a new version and after an inspection', () => {
    expect(needsNotice({ ...base, accepted: false })).toBe(true)
    expect(needsNotice(base)).toBe(false)
    expect(needsNotice({ ...base, inspections: [inspection], unseen: 1 })).toBe(true)
  })
  it('says retention, who can see, the purpose and that the assistant is an AI', () => {
    const lines = noticeLines({ ...base, ttlDays: 14 })
    expect(lines).toHaveLength(4)
    expect(lines[0]).toContain('14 days')
    expect(lines.join(' ')).toMatch(/owner/)
    expect(lines.join(' ')).toMatch(/accountability/)
    expect(lines.join(' ')).toMatch(/AI/)
    setLocale('it')
    expect(noticeLines(base)[0]).toContain('30 giorni')
  })
})

describe('the viewed by line', () => {
  it('names who, when and why', () => {
    const line = viewedLine(inspection)
    expect(line.startsWith('Viewed by olga@example.com on ')).toBe(true)
    expect(line.endsWith(': Checking a change to the caps reported by finance')).toBe(true)
    setLocale('it')
    expect(viewedLine(inspection).startsWith('Vista da olga@example.com il ')).toBe(true)
  })
})

describe('the voice follows data protection', () => {
  const local = [{ lang: 'it-IT', localService: true }]
  const online = [{ lang: 'it-IT', localService: false }]
  it('uses the online voice only when the data protection allows it', () => {
    expect(voiceKind('online', local, 'it')).toBe('online')
    const opts = voiceOptions('online')
    expect(opts.ttsOptions.browser.allowOnlineVoices).toBe(true)
  })
  it('otherwise a local voice of the system, else Piper with a lighter pitch', () => {
    expect(voiceKind('device', local, 'it')).toBe('system')
    expect(voiceKind('device', online, 'it')).toBe('piper')
    expect(voiceKind('device', [], 'en')).toBe('piper')
    expect(voiceKind('device', local, 'en')).toBe('piper')
    expect(voiceOptions('system').ttsOptions).toEqual({ piper: false, browser: { pitch: 1.3, rate: 1.05, allowOnlineVoices: false } })
    expect(voiceOptions('piper').ttsOptions).toEqual({ piper: { voice: 'it_IT-serena-high', pitchShift: 1.15 }, browser: { allowOnlineVoices: false } })
  })
  it('says which voice is in use', () => {
    expect(voiceText('online')).toMatch(/Google/)
    expect(voiceText('piper')).toMatch(/browser/)
    setLocale('it')
    expect(voiceText('system')).toMatch(/dispositivo/)
  })
})
