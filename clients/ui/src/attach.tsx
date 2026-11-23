/**
 * Attachments in the composer and in the conversation: the paper clip, drop and paste, previews before sending, and
 * the files of each question (images fetched with the session token). The service reads each file's kind from its
 * bytes and has the last word; these checks only spare a round trip.
 */
import type { AttachmentOffer, AttachmentRef } from '@evokai/evokai-client/browser'
import type { VNode } from 'preact'
import { useEffect, useRef, useState } from 'preact/hooks'

import { useUi } from './context'
import type { Controller } from './controller'
import { Icon } from './icons'
import { fill, type Strings } from './strings'

/** The panel's words for a refusal of the service (`attachment_*`), or null for any other error. */
export function fileError(s: Strings, code: string): string | null {
  if (code === 'attachment_unsupported') return s.fileUnsupported
  if (code === 'attachment_unreadable') return s.fileUnreadable
  if (code === 'attachments_full') return s.filesFull
  return /^(attachments?_|too_many_attachments)/.test(code) ? s.fileRefused : null
}

const ext = (kind: string) => (kind === 'jpeg' ? ['.jpg', '.jpeg'] : [`.${kind}`])
const size = (n: number) => (n >= 1e6 ? `${Math.round(n / 1e6)} MB` : `${Math.ceil(n / 1e3)} KB`)

/** The file picker's `accept`: the media types and the extensions of the kinds the session takes. */
export const accept = (o: AttachmentOffer): string => [...o.mediaTypes, ...o.kinds.flatMap(ext)].join(',')

function taken(o: AttachmentOffer, f: File): boolean {
  // Browsers type some files oddly (a CSV as a spreadsheet on Windows): the extension counts too.
  const e = `.${(f.name.split('.').pop() ?? '').toLowerCase()}`
  return o.mediaTypes.includes(f.type) || o.kinds.some((k) => ext(k).includes(e))
}

/** The files waiting to go with the next message. */
export function useFiles(offer: AttachmentOffer | null | undefined) {
  const { s } = useUi()
  const [files, setFiles] = useState<File[]>([])
  const [error, setError] = useState('')
  const add = (list: ArrayLike<File> | null | undefined) => {
    if (!offer || !list) return
    const picked = Array.from(list)
    // From the latest list: two drops before a render must not lose the first.
    setFiles((cur) => {
      const next = [...cur]
      let err = ''
      for (const f of picked) {
        if (!taken(offer, f)) err = fill(s.fileKind, { name: f.name })
        else if (f.size > offer.maxFileBytes) err = fill(s.fileTooLarge, { name: f.name, max: size(offer.maxFileBytes) })
        else if (next.length >= offer.maxFiles) err = fill(s.tooManyFiles, { n: String(offer.maxFiles) })
        else next.push(f)
      }
      setError(err)
      return next
    })
  }
  const set = (next: File[]) => {
    setFiles(next)
    setError('')
  }
  return { files, error, add, remove: (i: number) => set(files.filter((_, j) => j !== i)), clear: () => set([]) }
}

/** A local preview URL of a blob, revoked when it is no longer shown (none where the browser has none). */
function useUrl(blob: Blob | null): string | undefined {
  const [url, setUrl] = useState<string>()
  useEffect(() => {
    if (!blob || typeof URL.createObjectURL !== 'function') return
    const u = URL.createObjectURL(blob)
    setUrl(u)
    return () => URL.revokeObjectURL(u)
  }, [blob])
  return url
}

/** The paper clip and its hidden file picker. */
export function AttachButton({ offer, onFiles }: { offer: AttachmentOffer; onFiles: (f: ArrayLike<File> | null) => void }): VNode {
  const { s } = useUi()
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        hidden
        accept={accept(offer)}
        onChange={(e) => {
          onFiles(e.currentTarget.files)
          e.currentTarget.value = ''
        }}
      />
      <button type="button" class="cb" part="composer-button attach-button" aria-label={s.attach} title={s.attach} onClick={() => input.current?.click()}>
        <Icon d="clip" />
      </button>
    </>
  )
}

function Preview({ file, onRemove }: { file: File; onRemove: () => void }): VNode {
  const { s } = useUi()
  const url = useUrl(file.type.startsWith('image/') ? file : null)
  return (
    <span class="file" part="attachment-preview">
      {url ? <img src={url} alt="" /> : null}
      <span class="fname">{file.name}</span>
      <button type="button" part="attachment-remove" aria-label={fill(s.removeFile, { name: file.name })} onClick={onRemove}>
        <Icon d="x" size={12} />
      </button>
    </span>
  )
}

/** The files waiting to be sent, each removable, and why the last one was refused. */
export function Previews({ files, error, onRemove }: { files: File[]; error: string; onRemove: (i: number) => void }): VNode | null {
  if (!files.length && !error) return null
  return (
    <div class="files" part="attachments">
      {files.map((f, i) => (
        <Preview key={`${f.name}${i}`} file={f} onRemove={() => onRemove(i)} />
      ))}
      {error ? (
        <div class="err" part="attachment-error" role="alert">
          {error}
        </div>
      ) : null}
    </div>
  )
}

function Shown({ a, c }: { a: AttachmentRef; c: Controller }): VNode {
  const [blob, setBlob] = useState<Blob | null>(null)
  const image = a.mediaType.startsWith('image/')
  useEffect(() => {
    if (image && !a.url && a.id) c.session.attachment(a.id).then(setBlob, () => undefined)
  }, [a.id, a.url, image, c])
  const fetched = useUrl(blob)
  const src = a.url ?? fetched
  return src ? (
    <img class="shot" part="user-attachment" src={src} alt={a.name} title={a.name} />
  ) : (
    <span class="file" part="user-attachment">
      <span class="fname">{a.name}</span>
    </span>
  )
}

/** The files of a question, inside its bubble. */
export function MessageFiles({ items, c }: { items: AttachmentRef[] | undefined; c: Controller }): VNode | null {
  if (!items?.length) return null
  return (
    <div class="files" part="user-attachments">
      {items.map((a, i) => (
        <Shown key={a.id ?? i} a={a} c={c} />
      ))}
    </div>
  )
}
