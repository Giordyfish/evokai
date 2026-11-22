/**
 * The console assistant's session: `POST /v1/console/assistant/session` with the console cookie, for the selected
 * application. The token it returns belongs to the assistant's system application; what the assistant does acts on
 * the selected application with the person's own role, checked by the service on every call.
 */
import type { TokenInfo, TokenRequestContext } from '@client/browser/index'

import { adminJson } from '../api'

export interface AssistantSession {
  token: string
  expiresAt: number
  conversationId: string
  applicationId: string
  voiceEngine?: 'browser' | 'server'
}

/** A session token for the assistant acting on an application (a refresh continues the conversation). */
export async function assistantSession(applicationId: string, ctx?: TokenRequestContext): Promise<AssistantSession> {
  const body = ctx?.conversationId && ctx.reason === 'refresh' ? { conversationId: ctx.conversationId } : {}
  return adminJson<AssistantSession>(applicationId, 'POST', '/v1/console/assistant/session', body)
}

/** The `getToken` of `<evokai-chat>` for an application. */
export const tokenFor =
  (applicationId: string) =>
  async (ctx: TokenRequestContext): Promise<TokenInfo> => {
    const s = await assistantSession(applicationId, ctx)
    return { token: s.token, conversationId: s.conversationId, expiresAt: s.expiresAt, voiceEngine: s.voiceEngine ?? 'browser' }
  }

/** The voice the owner chose: the browser's engine, a Google voice when there is one, a little higher and faster. */
export const VOICE = {
  ttsOptions: {
    piper: false as const,
    browser: { pitch: 1.3, rate: 1.05, preferVoice: 'google', allowOnlineVoices: true },
  },
}
