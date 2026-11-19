import { useEffect, useRef } from 'preact/hooks'

import { masSrc, RM } from '../magic'
import { setTitle } from '../state'
import { Btn } from '../ui'
import { t } from '../i18n'

export function NotFound() {
  const img = useRef<HTMLImageElement>(null)
  setTitle(t('page.notFound'))
  useEffect(() => {
    if (RM()) return
    const t = setTimeout(() => {
      if (img.current) img.current.src = masSrc('blink', 120)
    }, 1800)
    return () => clearTimeout(t)
  }, [])
  return (
    <div class="empty" style="min-height:62vh;justify-content:center">
      <img ref={img} class="mas" src={masSrc('hop', 120)} width={120} height={120} alt="" />
      <h1>{t('notFound.title')}</h1>
      <p>{t('notFound.text')}</p>
      <Btn kind="pri" href="#/apps">
        {t('notFound.back')}
      </Btn>
    </div>
  )
}
