/**
 * The assistant in the shell: `<evokai-chat>` (from `clients/ui`) mounted once, its launcher at the bottom left, with
 * the live mascot as its orb. The mascot is one element that the chat moves between its places (the launcher, the
 * header, the middle of voice mode, the launcher again while voice is minimized) with the orb's own flight, and it
 * follows the chat's state. It is there only while the assistant is available and active (its activation is on the
 * Assistant page). Before the chat opens the first time it shows the first-use notice (once per login, and again when
 * someone viewed the person's conversations); the voice is the one the data protection allows.
 */
import { render } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import type { ChatState } from '@ui/chatstate'
import { defineElements, type EvokaiChatElement } from '@ui/elements'
import { ORB_MAX } from '@ui/travel'
import type { ConsoleApplication } from '@client/core/types'
import { adminJson, baseUrl, describe } from '../api'
import { SKIN, targetApp } from './launcher'
import { assistantActive, type AssistantState } from './choice'
import { type AssistantPrivacy, type LocalVoice, needsNotice, noticeLines, viewedLine, voiceKind, voiceOptions, voiceText } from './privacy'
import { tokenFor } from './session'
import { locale, t } from '../i18n'
import { LiveMascot } from '../mascot/LiveMascot'
import { mascotFor, type MascotView } from '../mascot/state'
import { go, useConsole } from '../state'
import { Btn, Icon } from '../ui'

/** The last application the person opened in this tab: the assistant keeps acting on it on pages without one. */
let lastApp: string | null = null

interface Mood {
  now: ChatState
  before: ChatState
  applied: boolean
  greeting: boolean
}

/** The voices of the browser, once it lists them (some browsers list them a moment after the page loads). */
function localVoices(): Promise<LocalVoice[]> {
  const synth = typeof speechSynthesis === 'undefined' ? null : speechSynthesis
  if (!synth) return Promise.resolve([])
  const now = synth.getVoices()
  if (now.length) return Promise.resolve(now)
  return new Promise((resolve) => {
    const done = () => resolve(synth.getVoices())
    synth.addEventListener('voiceschanged', done, { once: true })
    setTimeout(done, 800)
  })
}

/** The first-use notice: retention, who can see, purpose, the assistant's AI nature, and who viewed what. */
function Notice({ privacy, error, onAccept, onClose }: { privacy: AssistantPrivacy; error: string | null; onAccept: () => void; onClose: () => void }) {
  return (
    <section class="assistant-panel" role="dialog" aria-label={t('assistant.noticeTitle')}>
      <header>
        <b>{t('assistant.noticeTitle')}</b>
        <Btn kind="ic sm ghost" label={t('common.close')} onClick={onClose}>
          <Icon name="chev" style="rotate:90deg" />
        </Btn>
      </header>
      <div class="assistant-notice">
        <ul class="reqs">
          {noticeLines(privacy).map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {privacy.inspections.length ? (
          <>
            <h3>{t('assistant.viewedTitle')}</h3>
            {privacy.inspections.map((i) => (
              <p key={i.at + i.conversationId} class="hint viewed">
                {viewedLine(i)}
              </p>
            ))}
          </>
        ) : null}
        {error ? <p class="hint">{error}</p> : null}
        <Btn kind="pri" onClick={onAccept}>
          {privacy.accepted ? t('assistant.noticeSeen') : t('assistant.noticeAccept')}
        </Btn>
      </div>
    </section>
  )
}

/** A light-DOM child of `<evokai-chat>` in a named slot, whose content Preact renders. */
function useSlot(chat: EvokaiChatElement | null, name: string, cls: string): HTMLDivElement {
  const [slot] = useState(() => {
    const div = document.createElement('div')
    div.setAttribute('slot', name)
    div.className = cls
    return div
  })
  useEffect(() => {
    if (!chat) return
    chat.append(slot)
    return () => {
      render(null, slot)
      slot.remove()
    }
  }, [chat])
  return slot
}

/**
 * The assistant's launcher and chat, for the application the person is looking at (on a page without one: the last
 * one they opened, else the first they can see). Nothing at all while the assistant is not active for them.
 */
export function AssistantLauncher({ app, state, dark }: { app: ConsoleApplication | null; state: AssistantState | null; dark: boolean }) {
  const c = useConsole()
  if (app) lastApp = app.id
  const target = targetApp(app, lastApp, c.apps, state?.applicationId)
  const active = assistantActive(state) && !!target
  const [privacy, setPrivacy] = useState<{ app: string; p: AssistantPrivacy; voices: LocalVoice[] } | null>(null)
  const [notice, setNotice] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [chat, setChat] = useState<EvokaiChatElement | null>(null)
  const [mood, setMood] = useState<Mood>({ now: 'idle', before: 'idle', applied: false, greeting: false })
  const live = useRef({ privacy, notice })
  live.current = { privacy, notice }

  // What the person is told about the assistant, for the application it acts on (the voice depends on it).
  useEffect(() => {
    if (!active || !target) return
    let on = true
    Promise.all([adminJson<AssistantPrivacy>(target.id, 'GET', '/v1/console/assistant/privacy'), localVoices()])
      .then(([p, voices]) => on && setPrivacy({ app: target.id, p, voices }))
      .catch(() => on && setPrivacy(null))
    return () => {
      on = false
    }
  }, [active, target?.id])

  const ready = active && !!privacy

  // One chat for the whole console, created once the assistant is there and removed when it is not.
  useEffect(() => {
    if (!ready) return
    defineElements()
    const el = document.createElement('evokai-chat')
    el.setAttribute('base-url', baseUrl)
    el.setAttribute('title', t('assistant.panelTitle'))
    el.setAttribute('position', 'bottom-left')
    el.setAttribute('page-context', 'off')
    el.skin = SKIN
    const onState = (e: Event) => {
      const { state: now, previous } = (e as CustomEvent<{ state: ChatState; previous: ChatState }>).detail
      setMood((m) => ({ now, before: previous, applied: previous === 'confirm' && now === 'tool', greeting: m.greeting && now === 'idle' }))
    }
    const onNavigate = (e: Event) => {
      e.preventDefault()
      const path = (e as CustomEvent<{ path: string }>).detail.path
      if (path.startsWith('/') && !path.startsWith('//')) go(`#${path}`)
    }
    // The notice comes before the chat: opening it shows the notice instead until the person has read it.
    const onOpen = () => {
      const p = live.current.privacy?.p
      if (p && needsNotice(p)) {
        el.close()
        setNotice(true)
        return
      }
      setMood((m) => ({ ...m, greeting: m.now === 'idle' }))
    }
    el.addEventListener('statechange', onState)
    el.addEventListener('evokai-navigate', onNavigate)
    el.addEventListener('evokai-open', onOpen)
    document.body.append(el)
    setChat(el)
    return () => {
      el.removeEventListener('statechange', onState)
      el.removeEventListener('evokai-navigate', onNavigate)
      el.removeEventListener('evokai-open', onOpen)
      el.remove()
      setChat(null)
      setNotice(false)
    }
  }, [ready])

  // The application it acts on: a new token source, its own remembered conversation and its voice. The storage key
  // changes last, so the chat starts its new session with all three.
  useEffect(() => {
    if (!chat || !privacy) return
    chat.getToken = tokenFor(privacy.app)
    chat.voiceOptions = voiceOptions(voiceKind(privacy.p.voice, privacy.voices, locale()))
    chat.setAttribute('locale', locale())
    chat.setAttribute('storage-key', `evokai-console-assistant:${privacy.app}`)
  }, [chat, privacy])

  useEffect(() => {
    chat?.setAttribute('scheme', dark ? 'dark' : 'light')
  }, [chat, dark])

  const accept = async () => {
    if (!privacy) return
    try {
      await adminJson(privacy.app, 'POST', '/v1/console/assistant/privacy', { version: privacy.p.noticeVersion })
      setPrivacy({ ...privacy, p: { ...privacy.p, accepted: true, unseen: 0 } })
      setNotice(false)
      setError(null)
      // The chat opens once the notice is read (the next render has the accepted notice).
      setTimeout(() => chat?.open(), 0)
    } catch (e) {
      setError(describe(e))
    }
  }

  const view: MascotView = mascotFor(mood.now, mood.before, { applied: mood.applied, greeting: mood.greeting })
  const ended = () => setMood((m) => ({ ...m, applied: false, greeting: false }))
  return (
    <>
      {chat ? <Mascot chat={chat} view={view} onClipEnd={ended} /> : null}
      {chat && privacy ? <About chat={chat} app={c.apps.find((a) => a.id === privacy.app)?.name ?? ''} privacy={privacy.p} voices={privacy.voices} /> : null}
      {notice && privacy ? <Notice privacy={privacy.p} error={error} onAccept={() => void accept()} onClose={() => setNotice(false)} /> : null}
    </>
  )
}

/** The mascot as the chat's orb (`slot="orb"`): drawn once at the orb's largest size, moved and scaled by the chat. */
function Mascot(p: { chat: EvokaiChatElement; view: MascotView; onClipEnd: () => void }) {
  const slot = useSlot(p.chat, 'orb', 'assistant-mascot')
  useEffect(() => {
    render(<LiveMascot view={p.view} size={ORB_MAX} signals={p.chat.controller?.voice?.signals ?? null} onClipEnd={p.onClipEnd} />, slot)
  })
  return null
}

/** Above the suggestions of a new conversation: the application it acts on, the voice in use, who viewed what. */
function About(p: { chat: EvokaiChatElement; app: string; privacy: AssistantPrivacy; voices: LocalVoice[] }) {
  const slot = useSlot(p.chat, 'empty', 'assistant-about')
  useEffect(() => {
    render(
      <>
        {p.app ? <p class="acting">{t('assistant.actingOn', { app: p.app })}</p> : null}
        <p>{voiceText(voiceKind(p.privacy.voice, p.voices, locale()))}</p>
        {p.privacy.inspections.map((i) => (
          <p key={i.at + i.conversationId} class="viewed">
            {viewedLine(i)}
          </p>
        ))}
      </>,
      slot,
    )
  })
  return null
}
