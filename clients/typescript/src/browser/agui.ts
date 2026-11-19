/**
 * AG-UI (Agent User Interaction Protocol) over an Evokai session: `POST {baseUrl}/v1/session/agui` takes AG-UI's
 * `RunAgentInput` and streams AG-UI events. These are the types of the events Evokai sends (a subset of `@ag-ui/core`,
 * with the interrupt outcome of the protocol's human-in-the-loop) and a small runner. Frameworks that speak AG-UI
 * (CopilotKit's `HttpAgent`) can call the endpoint directly; this helper is for code that wants the events typed
 * without a dependency.
 */

import { defaultFetch, errorFromResponse, joinUrl, type FetchLike } from '../core/http'
import { readSse } from './sse'

export interface AguiMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'developer' | 'tool'
  content?: string | Array<{ type: 'text'; text: string }>
}

export interface AguiResume {
  interruptId: string
  status: 'resolved' | 'cancelled'
  /** `{approved: boolean}` for a confirmation, `{answer}` for a question. */
  payload?: unknown
}

export interface RunAgentInput {
  threadId: string
  runId: string
  messages: AguiMessage[]
  tools?: unknown[]
  context?: Array<{ description: string; value: string }>
  state?: unknown
  /** `{evokai: {page?, history?}}`: the open page, or the history of an external-storage conversation. */
  forwardedProps?: Record<string, unknown>
  resume?: AguiResume[]
}

export interface AguiInterrupt {
  id: string
  /** `confirmation` (an action that changes data) or `input_required` (a question with options). */
  reason: 'confirmation' | 'input_required' | (string & {})
  message?: string
  responseSchema?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export type AguiEvent =
  | { type: 'RUN_STARTED'; threadId: string; runId: string }
  | {
      type: 'RUN_FINISHED'
      threadId: string
      runId: string
      result?: { reason: string; conversationId: string; messages?: unknown[] }
      outcome?: { type: 'success' } | { type: 'interrupt'; interrupts: AguiInterrupt[] }
    }
  | { type: 'RUN_ERROR'; message: string; code?: string }
  | { type: 'TEXT_MESSAGE_START'; messageId: string; role: 'assistant' }
  | { type: 'TEXT_MESSAGE_CONTENT'; messageId: string; delta: string }
  | { type: 'TEXT_MESSAGE_END'; messageId: string }
  | { type: 'TOOL_CALL_START'; toolCallId: string; toolCallName: string; parentMessageId?: string }
  | { type: 'TOOL_CALL_END'; toolCallId: string }
  | { type: 'TOOL_CALL_RESULT'; messageId: string; toolCallId: string; content: string; role?: 'tool' }
  | { type: 'STATE_SNAPSHOT'; snapshot: { evokai: { conversationId: string; agent: string; turnId: string } } }
  | { type: 'STATE_DELTA'; delta: Array<{ op: string; path: string; value?: unknown }> }
  | {
      type: 'CUSTOM'
      /** `evokai.confirm`, `evokai.ask`, `evokai.chart`, `evokai.diagram`, `evokai.navigate`, `evokai.usage`... */
      name: string
      value: unknown
    }

export interface RunAguiOptions {
  /** The service's base URL with its base path, e.g. `https://app.example/evokai`. */
  baseUrl: string
  /** The session token. */
  token: string
  input: RunAgentInput
  fetch?: FetchLike
  /** Aborting it closes the stream, which cancels the turn. */
  signal?: AbortSignal
}

/** Run one AG-UI run and yield its events; throws an `EvokaiError` when the service refuses the run. */
export async function* runAgui(opts: RunAguiOptions): AsyncGenerator<AguiEvent> {
  const fetchFn = opts.fetch ?? defaultFetch()
  const init: RequestInit = {
    method: 'POST',
    headers: { Authorization: `Bearer ${opts.token}`, 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(opts.input),
  }
  if (opts.signal) init.signal = opts.signal
  const res = await fetchFn(joinUrl(opts.baseUrl, '/v1/session/agui'), init)
  if (!res.ok || !res.body) throw await errorFromResponse(res)
  for await (const m of readSse(res.body, opts.signal)) {
    if (!m.data) continue
    const event = JSON.parse(m.data) as AguiEvent
    yield event
    if (event.type === 'RUN_FINISHED' || event.type === 'RUN_ERROR') return
  }
}

/** The interrupts a finished run is waiting on (empty when it ended normally). */
export function interruptsOf(event: AguiEvent): AguiInterrupt[] {
  return event.type === 'RUN_FINISHED' && event.outcome?.type === 'interrupt' ? event.outcome.interrupts : []
}
