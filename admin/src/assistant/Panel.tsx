/**
 * The assistant in the shell: `<evokai-chat>` (from `clients/ui`) mounted once, its launcher at the bottom right, with
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
import { drivePlayground } from './drive'
import { pageContext, SKIN, sessionApp } from './launcher'
import { assistantActive, type AssistantState } from './choice'
import { type AssistantPrivacy, type LocalVoice, needsNotice, noticeLines, viewedLine, voiceKind, voiceOptions, voiceText } from './privacy'
import { tokenFor } from './session'
import { locale, t } from '../i18n'
import { LiveMascot } from '../mascot/LiveMascot'
import { mascotFor, type MascotView } from '../mascot/state'
import { go, useConsole } from '../state'
import { Btn, Icon } from '../ui'

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
  const box = useRef<HTMLElement>(null)
  // The focus moves into the notice (it opened from the launcher), Escape closes it.
  useEffect(() => box.current?.querySelector<HTMLElement>('.btn.pri')?.focus(), [])
  return (
    <section
      ref={box}
      class="assistant-panel"
      role="dialog"
      aria-label={t('assistant.noticeTitle')}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          onClose()
        }
      }}
    >
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
 * The assistant's launcher and chat: the platform's assistant, one conversation across the whole console, told which
 * page the person is on. Nothing at all while the assistant is not active for them.
 */
export function AssistantLauncher({ state, dark }: { state: AssistantState | null; dark: boolean }) {
  const c = useConsole()
  const target = sessionApp<ConsoleApplication>(c.apps, state?.applicationId)
  const active = assistantActive(state) && !!target
  const [privacy, setPrivacy] = useState<{ app: string; p: AssistantPrivacy; voices: LocalVoice[] } | null>(null)
  const [notice, setNotice] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [chat, setChat] = useState<EvokaiChatElement | null>(null)
  const [mood, setMood] = useState<Mood>({ now: 'idle', before: 'idle', applied: false, greeting: false })
  const [, setSwaps] = useState(0)
  // The mascot's place: a child of the chat from its first render, so the chat never draws an orb of its own.
  const [orbSlot] = useState(() => {
    const div = document.createElement('div')
    div.setAttribute('slot', 'orb')
    div.className = 'assistant-mascot'
    return div
  })
  const live = useRef({ privacy, notice })
  live.current = { privacy, notice }

  // What the person is told about the assistant (the voice depends on every application they see).
  useEffect(() => {
    if (!active || !target) return
    let on = true
    Promise.all([adminJson<AssistantPrivacy>(target.id, 'GET', '/v1/console/assistant/privacy'), localVoices()])
      .then(([p, voices]) => on && setPrivacy({ app: target.id, p, voices }))
      // A failure keeps what was read before (a running conversation goes on); with nothing read, no launcher.
      .catch(() => {})
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
    // The page the person is on (its console path and title, never its text): "this application" is the one in it.
    el.getPage = () => pageContext(location.hash, document.title)
    // The playground in front of the person: the assistant types there and reads the agent's answer.
    el.tools = { playground: (args) => drivePlayground(args, go) }
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
    el.append(orbSlot)
    document.body.append(el)
    setChat(el)
    return () => {
      render(null, orbSlot)
      el.removeEventListener('statechange', onState)
      el.removeEventListener('evokai-navigate', onNavigate)
      el.removeEventListener('evokai-open', onOpen)
      el.remove()
      setChat(null)
      setNotice(false)
    }
  }, [ready])

  // The token source (the console login of one application the person sees), the one remembered conversation and the
  // voice. The storage key is set last, so the chat starts its session with all three.
  const lang = locale()
  useEffect(() => {
    if (!chat || !privacy) return
    chat.getToken = tokenFor(privacy.app)
    chat.voiceOptions = voiceOptions(voiceKind(privacy.p.voice, privacy.voices, lang))
    chat.setAttribute('storage-key', 'evokai-console-assistant')
    // Draw again once the new controller exists: the mascot follows its voice.
    setTimeout(() => setSwaps((n) => n + 1), 0)
  }, [chat, privacy?.app, privacy?.p.voice])

  // The console's language and theme, also when the person changes them.
  useEffect(() => {
    if (!chat) return
    chat.setAttribute('locale', lang)
    chat.setAttribute('title', t('assistant.panelTitle'))
  }, [chat, lang])
  useEffect(() => {
    chat?.setAttribute('scheme', dark ? 'dark' : 'light')
  }, [chat, dark])
  // Until the notice is read the launcher cannot start voice; while it is open the launcher steps aside.
  const pending = !!privacy && needsNotice(privacy.p)
  useEffect(() => {
    if (!chat) return
    if (notice) chat.setAttribute('data-notice', 'open')
    else if (pending) chat.setAttribute('data-notice', 'pending')
    else chat.removeAttribute('data-notice')
  }, [chat, notice, pending])
  const closeNotice = () => {
    setNotice(false)
    setTimeout(() => chat?.shadowRoot?.querySelector<HTMLElement>('[part~="launcher"]')?.focus(), 0)
  }

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
      {chat ? <Mascot chat={chat} slot={orbSlot} view={view} onClipEnd={ended} /> : null}
      {chat && privacy ? <About chat={chat} privacy={privacy.p} voices={privacy.voices} /> : null}
      {notice && privacy ? <Notice privacy={privacy.p} error={error} onAccept={() => void accept()} onClose={closeNotice} /> : null}
    </>
  )
}

/** The mascot as the chat's orb (`slot="orb"`): drawn once at the orb's largest size, moved and scaled by the chat. */
function Mascot(p: { chat: EvokaiChatElement; slot: HTMLDivElement; view: MascotView; onClipEnd: () => void }) {
  useEffect(() => {
    render(<LiveMascot view={p.view} size={ORB_MAX} signals={p.chat.controller?.voice?.signals ?? null} onClipEnd={p.onClipEnd} />, p.slot)
  })
  return null
}

/** Above the suggestions of a new conversation: a line when the voice leaves the device, who viewed what. */
function About(p: { chat: EvokaiChatElement; privacy: AssistantPrivacy; voices: LocalVoice[] }) {
  const slot = useSlot(p.chat, 'empty', 'assistant-about')
  useEffect(() => {
    const voice = voiceText(voiceKind(p.privacy.voice, p.voices, locale()))
    render(
      <>
        {voice ? <p>{voice}</p> : null}
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
