/**
 * @evokai/evokai-ui: an interface for Evokai agents you can drop into any page.
 *
 *   import { defineElements } from '@evokai/evokai-ui'
 *   defineElements()
 *   <evokai-chat base-url="https://agents.example.com/agent" token-url="/api/agent/token" title="Ada"></evokai-chat>
 *
 * Transport, events, voice and usage come from the headless client (`@evokai/evokai-client/browser`).
 */
export { EvokaiChatElement, EvokaiVoiceElement, defineElements } from './elements'
export { chatState, fromEvent, type ChatState } from './chatstate'
export { Controller, pageContext } from './controller'
export type { ControllerOptions, ControllerState, ConversationItem, Installing, Support } from './controller'
export { defaultFormat } from './context'
export type { UiContext, ValueFormatter } from './context'
export { en, it, strings, stringsFor, fill } from './strings'
export type { Strings } from './strings'
export { DARK, LIGHT, ORB, themes, themeVars, resolveTheme, orbRgb } from './theme'
export type { OrbColors, OrbImages, OrbState, Palette, Theme } from './theme'
export { blocks, inline, safeHref } from './markdown'
export { ringShare, pctText } from './usage'
export { stagePhase, trapFocus } from './stage'
export type { Chart, Diagram, UsageShares } from '@evokai/evokai-client/browser'
