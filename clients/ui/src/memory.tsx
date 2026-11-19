/**
 * Long-term memory, as the person sees it: the view of what the agents remember (grouped by kind, each memory editable
 * and deletable, «forget everything», memory on or off for the person) and the one-time consent card. Every text comes
 * from `strings.ts`; every colour from the theme tokens.
 */
import type { JSX, VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import type { Memory, MemoryKind } from '@evokai/evokai-client/browser'
import { useUi } from './context'
import type { Controller, ControllerState } from './controller'
import { Icon } from './icons'
import { fill, type Strings } from './strings'

const KINDS: Array<[MemoryKind, keyof Strings]> = [
  ['preference', 'memoryPreference'],
  ['semantic', 'memorySemantic'],
  ['episodic', 'memoryEpisodic'],
]

/** A memory's date in the page's language: «3 Nov», with the year when it is not this year's. */
export function memoryDate(iso: string | null, lang: string, now = new Date()): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const year = d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' as const }
  return new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'short', ...year }).format(d)
}

/** Handle Escape here and keep it from closing the panel. */
function escape(e: KeyboardEvent, fn: () => void): void {
  if (e.key !== 'Escape') return
  e.preventDefault()
  e.stopPropagation()
  fn()
}

/** The service refused the text (instructions, secrets or personal data) or something else went wrong. */
function failure(e: unknown, s: Strings): string {
  const code = (e as { code?: unknown } | null)?.code
  return code === 'memory_refused' ? s.memoryRefused : s.memoryFailed
}

function Item({ m, c, onBusy, onGone }: { m: Memory; c: Controller; onBusy: (b: boolean) => void; onGone: () => void }): VNode {
  const { s, lang } = useUi()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(m.text)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)
  const editBtn = useRef<HTMLButtonElement>(null)
  const errId = `ek-merr-${m.id}`

  useEffect(() => {
    if (!editing) return
    const el = box.current
    el?.focus()
    el?.setSelectionRange?.(el.value.length, el.value.length)
  }, [editing])

  const stop = () => {
    setEditing(false)
    setError('')
    setDraft(m.text)
    setTimeout(() => editBtn.current?.focus(), 0)
  }
  const save = async () => {
    const text = draft.trim()
    if (!text || saving) return
    if (text === m.text) return stop()
    setSaving(true)
    onBusy(true)
    try {
      await c.editMemory(m.id, text)
      setEditing(false)
      setError('')
      setTimeout(() => editBtn.current?.focus(), 0)
    } catch (e) {
      setError(failure(e, s))
    } finally {
      setSaving(false)
      onBusy(false)
    }
  }
  const [removing, setRemoving] = useState(false)
  const remove = async () => {
    setRemoving(true)
    onBusy(true)
    try {
      await c.forgetMemory(m.id)
      onGone()
    } catch (e) {
      setError(failure(e, s))
      setRemoving(false)
    } finally {
      onBusy(false)
    }
  }

  const when = memoryDate(m.updatedAt ?? m.createdAt, lang)
  const meta = [when, m.source === 'person' ? s.memoryEditedByYou : ''].filter(Boolean).join(' · ')

  if (editing)
    return (
      <li class="mitem editing" part="memory-item" data-kind={m.kind} data-id={m.id}>
        <form
          class="medit"
          part="memory-editor"
          onSubmit={(e) => {
            e.preventDefault()
            void save()
          }}
        >
          <textarea
            ref={box}
            part="memory-input"
            value={draft}
            rows={3}
            aria-label={s.memoryEditLabel}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errId : undefined}
            onInput={(e) => {
              setDraft(e.currentTarget.value)
              if (error) setError('')
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
                e.preventDefault()
                void save()
              } else escape(e, stop)
            }}
          />
          {error ? (
            <p id={errId} class="err" part="memory-error" role="alert">
              {error}
            </p>
          ) : null}
          <div class="end">
            <button type="button" class="btn" part="memory-cancel" onClick={stop}>
              {s.cancel}
            </button>
            <button type="submit" class="btn primary" part="memory-save" disabled={saving || !draft.trim()}>
              {s.memorySave}
            </button>
          </div>
        </form>
      </li>
    )
  return (
    <li class="mitem" part="memory-item" data-kind={m.kind} data-id={m.id}>
      <div class="mbody">
        <p class="mtext" part="memory-text">
          {m.text}
        </p>
        {meta ? (
          <p class="mmeta" part="memory-meta">
            {meta}
          </p>
        ) : null}
        {error ? (
          <p class="err" part="memory-error" role="alert">
            {error}
          </p>
        ) : null}
      </div>
      <div class="mact">
        <button
          ref={editBtn}
          type="button"
          class="ib"
          part="memory-edit"
          aria-label={s.memoryEditLabel}
          title={s.memoryEdit}
          onClick={() => {
            setDraft(m.text)
            setError('')
            setEditing(true)
          }}
        >
          <Icon d="edit" size={15} />
        </button>
        <button type="button" class="ib" part="memory-delete" aria-label={s.memoryDeleteLabel} title={s.memoryDelete} disabled={removing} onClick={() => void remove()}>
          <Icon d="trash" size={15} />
        </button>
      </div>
    </li>
  )
}

/** An inline confirmation: the question, then the action and «Cancel». */
function Confirm(props: { text: string; action: string; part: string; onYes: () => Promise<void>; onNo: () => void }): VNode {
  const { s } = useUi()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const no = useRef<HTMLButtonElement>(null)
  useEffect(() => no.current?.focus(), [])
  return (
    <div class="mconfirm" part="memory-confirm" role="alertdialog" aria-label={props.text} onKeyDown={(e) => escape(e, props.onNo)}>
      <p>{props.text}</p>
      {error ? (
        <p class="err" part="memory-error" role="alert">
          {error}
        </p>
      ) : null}
      <div class="end">
        <button ref={no} type="button" class="btn" onClick={props.onNo}>
          {s.cancel}
        </button>
        <button
          type="button"
          class="btn danger"
          part={props.part}
          disabled={busy}
          onClick={() => {
            setBusy(true)
            props.onYes().then(
              () => setBusy(false),
              (e: unknown) => {
                setBusy(false)
                setError(failure(e, s))
              },
            )
          }}
        >
          {props.action}
        </button>
      </div>
    </div>
  )
}

export interface MemoryViewProps {
  controller: Controller
  state: ControllerState
  /** Back to the conversation (the panel gives the focus back). */
  onClose: () => void
}

/** «What {title} remembers»: replaces the conversation in the panel until the person goes back. */
export function MemoryView({ controller: c, state: st, onClose }: MemoryViewProps): VNode {
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  const [asking, setAsking] = useState<'forget' | 'off' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const title = useRef<HTMLHeadingElement>(null)
  useEffect(() => title.current?.focus(), [])

  const m = st.memory
  const items = st.memories
  const active = !!m?.active
  const loading = !items && !st.memoryError
  const turnOn = async () => {
    setBusy(true)
    setError('')
    try {
      await c.setMemoryConsent(true)
      await c.loadMemories()
    } catch (e) {
      setError(failure(e, s))
    } finally {
      setBusy(false)
    }
  }
  const onKey = (e: JSX.TargetedKeyboardEvent<HTMLElement>) => {
    if (!e.defaultPrevented) escape(e, asking ? () => setAsking(null) : onClose)
  }
  const done = () => {
    setAsking(null)
    setTimeout(() => title.current?.focus(), 0)
  }

  return (
    <section class="memory" part="memory-view" aria-labelledby="ek-mtitle" aria-busy={loading || busy} onKeyDown={onKey}>
      <div class="mhead">
        <button type="button" class="ib" part="memory-back" aria-label={s.memoryBack} title={s.memoryBack} onClick={onClose}>
          <Icon d="back" />
        </button>
        <h2 id="ek-mtitle" class="mtitle" part="memory-title" tabIndex={-1} ref={title}>
          {t(s.memoryTitle)}
        </h2>
      </div>
      <div class="mscroll">
        {st.memoryError ? (
          <div class="mstate" part="memory-error">
            <p>{s.memoryLoadFailed}</p>
            <button type="button" class="btn" onClick={() => void c.loadMemories()}>
              {s.retry}
            </button>
          </div>
        ) : loading ? (
          <div class="mstate" role="status">
            <span class="spin" aria-hidden="true" />
            <span class="sub">{s.memoryLoading}</span>
          </div>
        ) : !active ? (
          <div class="mstate moff" part="memory-off">
            <p>{t(s.memoryOff)}</p>
            {error ? (
              <p class="err" role="alert">
                {error}
              </p>
            ) : null}
            <button type="button" class="btn primary" part="memory-consent" data-action="on" disabled={busy} onClick={() => void turnOn()}>
              {s.memoryTurnOn}
            </button>
          </div>
        ) : (
          <>
            <p class="mintro sub">{t(s.memoryIntro)}</p>
            {items && items.length === 0 ? (
              <p class="mstate sub" part="memory-empty">
                {t(s.memoryEmpty)}
              </p>
            ) : (
              KINDS.map(([kind, label]) => {
                const group = (items ?? []).filter((x) => x.kind === kind)
                if (!group.length) return null
                const id = `ek-mg-${kind}`
                return (
                  <section key={kind} class="mgroup" part="memory-group" data-kind={kind} aria-labelledby={id}>
                    <h3 id={id}>{s[label] as string}</h3>
                    <ul>
                      {group.map((x) => (
                        <Item key={x.id} m={x} c={c} onBusy={setBusy} onGone={() => setTimeout(() => title.current?.focus(), 0)} />
                      ))}
                    </ul>
                  </section>
                )
              })
            )}
          </>
        )}
      </div>
      {active && items ? (
        <div class="mfoot">
          {asking === 'forget' ? (
            <Confirm
              text={s.memoryForgetAllConfirm}
              action={s.memoryForgetAll}
              part="memory-forget-all memory-confirm-yes"
              onYes={async () => {
                await c.forgetMemory()
                done()
              }}
              onNo={() => setAsking(null)}
            />
          ) : asking === 'off' ? (
            <Confirm
              text={s.memoryTurnOffConfirm}
              action={s.memoryTurnOffAction}
              part="memory-consent memory-confirm-yes"
              onYes={async () => {
                await c.setMemoryConsent(false)
                done()
              }}
              onNo={() => setAsking(null)}
            />
          ) : (
            <>
              {items.length ? (
                <button type="button" class="btn quiet danger" part="memory-forget-all" disabled={busy} onClick={() => setAsking('forget')}>
                  {s.memoryForgetAll}
                </button>
              ) : null}
              <button type="button" class="btn quiet" part="memory-consent" data-action="off" disabled={busy} onClick={() => setAsking('off')}>
                {s.memoryTurnOff}
              </button>
            </>
          )}
        </div>
      ) : null}
    </section>
  )
}

/** The one-time question, above the composer: never blocks the conversation. */
export function MemoryAsk({ c }: { c: Controller }): VNode {
  const { s, names } = useUi()
  const t = (x: string) => fill(x, { title: names.title, app: names.app })
  return (
    <div class="mask" part="memory-ask" role="region" aria-label={s.memoryAskLabel}>
      <p>{t(s.memoryAsk)}</p>
      <div class="end">
        <button type="button" class="btn" part="memory-ask-no" onClick={() => void c.setMemoryConsent(false, true).catch(() => undefined)}>
          {s.memoryAskNo}
        </button>
        <button type="button" class="btn primary" part="memory-ask-yes" onClick={() => void c.setMemoryConsent(true, true).catch(() => undefined)}>
          {s.memoryAskYes}
        </button>
      </div>
    </div>
  )
}
