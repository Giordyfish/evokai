/** What every page shows while its data loads, when it fails, and when a page does not exist. */
import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'

import { describe } from '../api'
import { Mage, PageLoader, toast } from '../magic'
import type { Loaded } from '../state'
import { Btn, Panel } from '../ui'
import { t } from '../i18n'

export function LoadError({ what, error, retry }: { what: string; error: unknown; retry: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  return (
    <div class="empty">
      <Mage k="blink" size={96} cls="sad" />
      <p>
        <b>{t('common.loadError', { what })}</b>
        <br />
        {describe(error)} {t('common.nothingChanged')}
      </p>
      <Btn
        busy={busy ? t('common.tryingAgain') : false}
        onClick={async () => {
          setBusy(true)
          await retry()
          setBusy(false)
          toast(t('common.loaded'), t('common.serverAnswered'), null)
        }}
      >
        {t('common.tryAgain')}
      </Btn>
    </div>
  )
}

/**
 * The body of a page from its data: the rune loader the first time, the error block on failure, the content once
 * loaded (kept while reloading).
 */
export function Loads<T>(p: { load: Loaded<T>; what: string; children: (data: T) => ComponentChildren; panel?: boolean }) {
  const { data, error, reload } = p.load
  if (data !== undefined) return <>{p.children(data)}</>
  if (error)
    return p.panel === false ? (
      <LoadError what={p.what} error={error} retry={reload} />
    ) : (
      <Panel>
        <LoadError what={p.what} error={error} retry={reload} />
      </Panel>
    )
  return <PageLoader what={p.what} />
}
