/**
 * The assistant's panel, opened from the shell. While the assistant has no model it shows the activation screen (a
 * link to the Assistant page for its owners). Once active it embeds `<evokai-chat>` (from `clients/ui`) acting on the
 * selected application, with the live mascot in the panel's header slot following the chat's state. Before the
 * chat it shows the first-use notice (once per login, and again when someone viewed the person's conversations); the
 * voice is the one the data protection allows.
 */
import { render } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import type { ChatState } from '@ui/chatstate'
import { defineElements, type EvokaiChatElement } from '@ui/elements'
import type { ConsoleApplication } from '@client/core/types'
import type { AudioSignals } from '@client/browser/index'
import { adminJson, baseUrl, describe } from '../api'
import type { AssistantState } from './choice'
import { type AssistantPrivacy, type LocalVoice, needsNotice, noticeLines, viewedLine, voiceKind, voiceOptions, voiceText } from './privacy'
import { tokenFor } from './session'
import { locale, t } from '../i18n'
import { LiveMascot } from '../mascot/LiveMascot'
import { mascotFor, type MascotView } from '../mascot/state'
import { go, useConsole } from '../state'
import { Btn, Icon } from '../ui'

/** The chat's look inside the console: no launcher (the shell opens it), a larger header for the mascot. */
const SKIN = `
.dock{display:none}
.orbfly{display:none}
.oslot.head{width:84px;height:84px;margin-right:0;overflow:visible}
.oslot.mid{width:200px;height:200px}
`

const THEME = { logo: 'fx/still-128.png', orb: {} }

function Chat({ app, privacy, voices, onClose }: { app: ConsoleApplication; privacy: AssistantPrivacy; voices: LocalVoice[]; onClose: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<{ now: ChatState; before: ChatState; applied: boolean; greeting: boolean }>({ now: 'idle', before: 'idle', applied: false, greeting: true })
  const [chat, setChat] = useState<EvokaiChatElement | null>(null)
  const [voice, setVoice] = useState(false)

  useEffect(() => {
    defineElements()
    const el = document.createElement('evokai-chat')
    el.setAttribute('base-url', baseUrl)
    el.setAttribute('locale', locale())
    el.setAttribute('title', t('assistant.panelTitle'))
    el.setAttribute('shortcut', 'none')
    el.setAttribute('page-context', 'off')
    el.setAttribute('storage-key', `evokai-console-assistant:${app.id}`)
    el.getToken = tokenFor(app.id)
    el.voiceOptions = voiceOptions(voiceKind(privacy.voice, voices, locale()))
    el.theme = THEME
    el.skin = SKIN
    const onState = (e: Event) => {
      const { state: now, previous } = (e as CustomEvent<{ state: ChatState; previous: ChatState }>).detail
      setState((s) => ({ now, before: previous, applied: previous === 'confirm' && now === 'tool', greeting: s.greeting && now === 'idle' }))
    }
    const onNavigate = (e: Event) => {
      e.preventDefault()
      const path = (e as CustomEvent<{ path: string }>).detail.path
      if (path.startsWith('/') && !path.startsWith('//')) go(`#${path}`)
    }
    el.addEventListener('statechange', onState)
    el.addEventListener('evokai-navigate', onNavigate)
    el.addEventListener('evokai-close', onClose)
    host.current?.append(el)
    el.open()
    setChat(el)
    // In voice mode the mascot moves to the middle of the voice stage, where listening and speaking happen.
    const watch = new MutationObserver(() => setVoice(el.hasAttribute('data-voice')))
    watch.observe(el, { attributes: true, attributeFilter: ['data-voice'] })
    return () => {
      watch.disconnect()
      el.removeEventListener('statechange', onState)
      el.removeEventListener('evokai-navigate', onNavigate)
      el.removeEventListener('evokai-close', onClose)
      el.remove()
    }
  }, [app.id])

  const view: MascotView = mascotFor(state.now, state.before, { applied: state.applied, greeting: state.greeting })
  const ended = () => setState((s) => ({ ...s, applied: false, greeting: false }))
  return (
    <div ref={host} class="assistant-host" data-state={state.now}>
      {chat ? <MascotSlot chat={chat} voice={voice} view={view} signals={chat.controller?.voice?.signals ?? null} onClipEnd={ended} /> : null}
      {chat ? <AboutSlot chat={chat} privacy={privacy} voices={voices} /> : null}
    </div>
  )
}

/** Under the suggestions of a new conversation: the voice in use and who viewed the person's conversations. */
function AboutSlot(p: { chat: EvokaiChatElement; privacy: AssistantPrivacy; voices: LocalVoice[] }) {
  const [slot] = useState(() => {
    const div = document.createElement('div')
    div.setAttribute('slot', 'empty')
    div.className = 'assistant-about'
    return div
  })
  useEffect(() => {
    p.chat.append(slot)
    return () => {
      render(null, slot)
      slot.remove()
    }
  }, [p.chat])
  useEffect(() => {
    render(
      <>
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
function Notice({ privacy, onAccept, onClose }: { privacy: AssistantPrivacy; onAccept: () => void; onClose: () => void }) {
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
        <Btn kind="pri" onClick={onAccept}>
          {privacy.accepted ? t('assistant.noticeSeen') : t('assistant.noticeAccept')}
        </Btn>
      </div>
    </section>
  )
}

/** The mascot as a light-DOM child of `<evokai-chat>`: in its `avatar` slot (the header), `stage-orb` in voice mode. */
function MascotSlot(p: { chat: EvokaiChatElement; voice: boolean; view: MascotView; signals: AudioSignals | null; onClipEnd: () => void }) {
  const [slot] = useState(() => {
    const div = document.createElement('div')
    div.setAttribute('slot', 'avatar')
    div.className = 'assistant-mascot'
    return div
  })
  useEffect(() => {
    p.chat.append(slot)
    return () => {
      render(null, slot)
      slot.remove()
    }
  }, [p.chat])
  useEffect(() => {
    slot.setAttribute('slot', p.voice ? 'stage-orb' : 'avatar')
    render(<LiveMascot view={p.view} size={p.voice ? 200 : 84} signals={p.signals} onClipEnd={p.onClipEnd} />, slot)
  })
  return null
}

/** The panel: the activation screen while the assistant has no model, else the chat on the selected application. */
export function AssistantPanel({ app, open, onClose }: { app: ConsoleApplication | null; open: boolean; onClose: () => void }) {
  const c = useConsole()
  const [state, setState] = useState<AssistantState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [privacy, setPrivacy] = useState<{ p: AssistantPrivacy; voices: LocalVoice[] } | null>(null)
  const any = app?.id ?? c.apps[0]?.id ?? ''
  const active = !!state?.active && !!app

  useEffect(() => {
    if (!open || !any) return
    adminJson<AssistantState>(any, 'GET', '/v1/admin/assistant')
      .then((s) => {
        setState(s)
        setError(null)
      })
      .catch((e) => setError(describe(e)))
  }, [open, any])

  useEffect(() => {
    if (!open || !active || !app) return
    setPrivacy(null)
    Promise.all([adminJson<AssistantPrivacy>(app.id, 'GET', '/v1/console/assistant/privacy'), localVoices()])
      .then(([p, voices]) => setPrivacy({ p, voices }))
      .catch((e) => setError(describe(e)))
  }, [open, active, app?.id])

  const accept = async () => {
    if (!app || !privacy) return
    try {
      await adminJson(app.id, 'POST', '/v1/console/assistant/privacy', { version: privacy.p.noticeVersion })
      setPrivacy({ ...privacy, p: { ...privacy.p, accepted: true, unseen: 0 } })
    } catch (e) {
      setError(describe(e))
    }
  }

  if (!open) return null
  if (active && app && privacy && !error) {
    if (needsNotice(privacy.p)) return <Notice privacy={privacy.p} onAccept={() => void accept()} onClose={onClose} />
    return <Chat app={app} privacy={privacy.p} voices={privacy.voices} onClose={onClose} />
  }
  const system = state?.applicationId ? c.apps.find((a) => a.id === state.applicationId) : undefined
  return (
    <section class="assistant-panel" role="dialog" aria-label={t('assistant.panelTitle')}>
      <header>
        <b>{t('assistant.panelTitle')}</b>
        <Btn kind="ic sm ghost" label={t('common.close')} onClick={onClose}>
          <Icon name="chev" style="rotate:90deg" />
        </Btn>
      </header>
      <div class="assistant-off">
        <LiveMascot view={{ kind: 'clip', name: 'idle-combo', loop: true }} size={160} />
        {error ? (
          <p class="hint">{error}</p>
        ) : !state ? (
          <p class="hint">{t('assistant.loading')}</p>
        ) : !state.available ? (
          <p class="hint">{t('assistant.unavailable')}</p>
        ) : !state.active ? (
          <>
            <h3>{t('assistant.offTitle')}</h3>
            <p class="hint">{t('assistant.offText')}</p>
            {system?.role === 'owner' ? (
              <Btn
                kind="pri"
                onClick={() => {
                  onClose()
                  go('#/assistant')
                }}
              >
                {t('assistant.offActivate')}
              </Btn>
            ) : (
              <p class="hint">{t('assistant.offAskOwner')}</p>
            )}
          </>
        ) : (
          <p class="hint">{t('assistant.pickApp')}</p>
        )}
      </div>
    </section>
  )
}
