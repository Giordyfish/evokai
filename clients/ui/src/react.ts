/**
 * React wrappers: `<EvokaiChat>` and `<EvokaiVoice>` render the Web Components, set the function and object
 * properties on them and forward their events as callbacks. The elements are registered on first render.
 */
import { createElement, forwardRef, useEffect, useImperativeHandle, useRef, type ReactElement, type ReactNode, type Ref } from 'react'

import type { ClientToolHandler, CreateVoiceOptions, EvokaiSession, PageContext, SessionEvent, TokenInfo, TokenRequestContext } from '@evokai/evokai-client/browser'
import type { ToolRenderer, ValueFormatter } from './context'
import type { ConversationItem } from './controller'
import { defineElements, type EvokaiChatElement, type EvokaiVoiceElement } from './elements'
import type { Strings } from './strings'
import type { Theme } from './theme'

export interface EvokaiCommonProps {
  baseUrl?: string
  tokenUrl?: string
  conversationsUrl?: string
  getToken?: (ctx: TokenRequestContext) => TokenInfo | Promise<TokenInfo>
  session?: EvokaiSession
  listConversations?: () => Promise<ConversationItem[]>
  getPage?: (() => PageContext | null | undefined) | null
  locale?: 'en' | 'it' | (string & {})
  /** A preset name (`evokai`) or a theme object. */
  theme?: Theme | string
  /** Your CSS inside the component: it always wins over the default look (see the README, «Skins»). */
  skin?: string | string[]
  /** Drop the default look: only the tokens and a reset remain, the skin draws everything. */
  unstyled?: boolean
  /** Replaced icons by name: SVG markup. */
  icons?: Partial<Record<string, string>>
  /** Elements for the slots (`slot="orb"`, `"launcher"`, `"avatar"`, `"empty"`, `"stage-orb"`). */
  children?: ReactNode
  strings?: Partial<Strings>
  toolLabels?: Record<string, string> | ((name: string) => string | null | undefined)
  /** Your own cards for tools, by tool name (see `EvokaiChatElement.toolRenderers`). */
  toolRenderers?: Record<string, ToolRenderer>
  /** Your custom client tools, by name (see `EvokaiChatElement.tools`). */
  tools?: Record<string, ClientToolHandler>
  formatValue?: ValueFormatter
  voiceOptions?: CreateVoiceOptions
  title?: string
  appName?: string
  storageKey?: string
  scheme?: 'light' | 'dark'
  /** Every session event. */
  onEvent?: (e: SessionEvent) => void
  /** The agent asked to open a page; call `preventDefault()` to route it yourself. */
  onNavigate?: (path: string, e: CustomEvent<{ path: string }>) => void
  className?: string
}

export interface EvokaiChatProps extends EvokaiCommonProps {
  open?: boolean
  voice?: boolean
  position?: 'bottom-right' | 'bottom-left'
  /** The key that with Ctrl/⌘ toggles the panel (default `j`; `none`). */
  shortcut?: string
  onOpenChange?: (open: boolean) => void
}

export interface EvokaiVoiceProps extends EvokaiCommonProps {
  /** The id of an `<evokai-chat>` whose conversation the stage continues. */
  for?: string
  onEnd?: () => void
}

type Props = EvokaiCommonProps & Partial<EvokaiChatProps & EvokaiVoiceProps>

const PROPS = ['getToken', 'session', 'listConversations', 'getPage', 'strings', 'toolLabels', 'toolRenderers', 'tools', 'formatValue', 'voiceOptions', 'skin', 'icons'] as const

function attrs(p: Props): Record<string, string | undefined> {
  const str = (v: unknown) => (v === undefined || v === null || v === false ? undefined : v === true ? '' : String(v))
  return {
    'base-url': str(p.baseUrl),
    'token-url': str(p.tokenUrl),
    'conversations-url': str(p.conversationsUrl),
    locale: str(p.locale),
    title: str(p.title),
    'app-name': str(p.appName),
    'storage-key': str(p.storageKey),
    scheme: str(p.scheme),
    position: str(p.position),
    shortcut: str(p.shortcut),
    voice: p.voice === false ? 'off' : undefined,
    unstyled: p.unstyled ? '' : undefined,
    for: str(p.for),
    theme: typeof p.theme === 'string' ? p.theme : undefined,
    class: str(p.className),
  }
}

function useElement<E extends HTMLElement>(tag: 'evokai-chat' | 'evokai-voice', p: Props, ref: Ref<E | null>): ReactElement {
  const el = useRef<E | null>(null)
  useImperativeHandle(ref, () => el.current as E, [])
  defineElements()
  useEffect(() => {
    const e = el.current as unknown as Record<string, unknown> | null
    if (!e) return
    for (const k of PROPS) if (p[k] !== undefined) e[k] = p[k]
    if (typeof p.theme === 'object') e.theme = p.theme
    ;(e as unknown as { update(): void }).update()
  })
  useEffect(() => {
    const e = el.current
    if (!e) return
    const on = (name: string, fn: (ev: Event) => void) => {
      e.addEventListener(name, fn)
      return () => e.removeEventListener(name, fn)
    }
    const offs = [
      on('evokai-event', (ev) => p.onEvent?.((ev as CustomEvent<SessionEvent>).detail)),
      on('evokai-navigate', (ev) => p.onNavigate?.((ev as CustomEvent<{ path: string }>).detail.path, ev as CustomEvent<{ path: string }>)),
      on('evokai-open', () => p.onOpenChange?.(true)),
      on('evokai-close', () => p.onOpenChange?.(false)),
      on('evokai-voice-end', () => p.onEnd?.()),
    ]
    return () => offs.forEach((off) => off())
  })
  useEffect(() => {
    const e = el.current as unknown as EvokaiChatElement | null
    if (!e || tag !== 'evokai-chat' || p.open === undefined) return
    if (p.open) e.open()
    else e.close()
  }, [p.open, tag])
  const a = Object.fromEntries(Object.entries(attrs(p)).filter(([, v]) => v !== undefined))
  return createElement(tag, { ...a, ref: el }, p.children)
}

/** The launcher and chat panel. `ref` gives the element (`open()`, `close()`, `send(text)`, `startVoice()`). */
export const EvokaiChat = forwardRef<EvokaiChatElement | null, EvokaiChatProps>(function EvokaiChat(props, ref) {
  return useElement<EvokaiChatElement>('evokai-chat', props, ref)
})

/** The voice stage on its own. `ref` gives the element (`start()`, `stop()`). */
export const EvokaiVoice = forwardRef<EvokaiVoiceElement | null, EvokaiVoiceProps>(function EvokaiVoice(props, ref) {
  return useElement<EvokaiVoiceElement>('evokai-voice', props, ref)
})

export type { EvokaiChatElement, EvokaiVoiceElement }
export type { ToolRenderer }
