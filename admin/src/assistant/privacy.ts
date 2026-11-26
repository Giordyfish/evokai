/**
 * What the console tells a person about the assistant (`GET /v1/console/assistant/privacy`): the first-use notice,
 * who viewed their conversations and why, and the voice their application's data protection allows.
 */
import { dateTime } from '../format'
import { t } from '../i18n'

export type VoiceChoice = 'online' | 'device'

export interface Inspection {
  conversationId: string
  by: string
  at: string
  reason: string
}

export interface AssistantPrivacy {
  noticeVersion: number
  accepted: boolean
  acceptedAt: string | null
  ttlDays: number | null
  voice: VoiceChoice
  inspections: Inspection[]
  unseen: number
}

/** The notice opens before the chat the first time, after a new version, and when someone viewed a conversation since. */
export const needsNotice = (p: AssistantPrivacy): boolean => !p.accepted || p.unseen > 0

/** The notice, a line per point: retention, who can see, purpose, the assistant's AI nature. */
export function noticeLines(p: AssistantPrivacy): string[] {
  return [
    t('assistant.noticeRetention', { days: p.ttlDays ?? 30 }),
    t('assistant.noticeWho'),
    t('assistant.noticePurpose'),
    t('assistant.noticeAi'),
  ]
}

/** "Viewed by <name> on <date>: <reason>". */
export const viewedLine = (i: Inspection): string => t('assistant.viewedBy', { name: i.by, date: dateTime(i.at), reason: i.reason })

/** A voice the browser offers (`speechSynthesis.getVoices()`). */
export interface LocalVoice {
  lang: string
  localService: boolean
}

export type VoiceKind = 'online' | 'system' | 'piper'

/**
 * Which voice speaks: the browser's online voice (Google) only when the data protection allows processors outside the
 * EU; otherwise a voice of the operating system for the language when there is one, else Piper in the browser.
 */
export function voiceKind(choice: VoiceChoice, voices: LocalVoice[], locale: string): VoiceKind {
  if (choice === 'online') return 'online'
  const lang = locale.slice(0, 2).toLowerCase()
  return voices.some((v) => v.localService && v.lang.toLowerCase().startsWith(lang)) ? 'system' : 'piper'
}

/** The `voiceOptions` of `<evokai-chat>` for a voice. Recognition stays on the device in every case. */
export function voiceOptions(kind: VoiceKind) {
  if (kind === 'online') return { ttsOptions: { piper: false as const, browser: { pitch: 1.3, rate: 1.05, preferVoice: 'google', allowOnlineVoices: true } } }
  if (kind === 'system') return { ttsOptions: { piper: false as const, browser: { pitch: 1.3, rate: 1.05, allowOnlineVoices: false } } }
  return { ttsOptions: { piper: { voice: 'it_IT-serena-high', pitchShift: 1.15 }, browser: { allowOnlineVoices: false } } }
}

/** What the panel says about the voice: only the online voice, the one that sends the text off the device. */
export const voiceText = (kind: VoiceKind): string | null => (kind === 'online' ? t('assistant.voiceOnline') : null)
