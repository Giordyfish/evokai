/**
 * Settings, Modules (deployment owners): which parts of Evokai this deployment runs, applications and gateway. A module
 * turned off hides its pages, answers its routes with `module_disabled` and stops its background jobs; its data stays.
 * When `EVOKAI_MODULES` sets them the page is read-only. A change asks for confirmation and writes an audit row.
 */
import { useState } from 'preact/hooks'

import type { EvokaiModule, ModulesView } from '@client/core/types'
import { consoleJson, describe } from '../api'
import { done, Empty, sparkle, toast } from '../magic'
import { moduleOn } from '../moduleSwitch'
import { setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, Panel, Switch, Tag } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'

const PATH = '/v1/console/modules'
const ORDER: EvokaiModule[] = ['applications', 'gateway']

const names = (list: EvokaiModule[]): string => list.map((m) => t(`modules.${m}`)).join(', ')

export function Modules() {
  setTitle(t('page.modules'))
  const load = useLoad(() => consoleJson<ModulesView>('GET', PATH), [])
  return (
    <>
      <Hero title={t('page.modules')} sub={t('modules.sub')} />
      <Loads load={load} what={t('what.modules')}>
        {(v) => <Switches key={v.enabled.join(',') + String(v.pinned)} view={v} onSaved={(x) => load.set(x)} />}
      </Loads>
    </>
  )
}

function Switches({ view, onSaved }: { view: ModulesView; onSaved: (v: ModulesView) => void }) {
  const c = useConsole()
  const [draft, setDraft] = useState<EvokaiModule[]>(view.enabled)
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const off = view.enabled.filter((m) => !draft.includes(m))
  const on = draft.filter((m) => !view.enabled.includes(m))
  const changed = off.length + on.length > 0

  const flip = (m: EvokaiModule, value: boolean) => {
    setError(null)
    setAsking(false)
    const next = ORDER.filter((x) => (x === m ? value : draft.includes(x)))
    if (!next.length) {
      setError(t('modules.atLeastOne'))
      return
    }
    setDraft(next)
  }

  const save = async (e: Event) => {
    const btn = e.currentTarget as Element
    setBusy(true)
    setError(null)
    try {
      const v = await consoleJson<ModulesView>('PUT', PATH, { enabled: draft, confirm: true })
      done(btn)
      sparkle(btn)
      toast(t('modules.saved'), t('modules.savedSub'))
      setAsking(false)
      onSaved(v)
      await c.reloadMe()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title={t('modules.title')} actions={view.pinned ? <Tag tone="gold">{t('modules.fromConfig')}</Tag> : null}>
      {view.pinned ? <p class="hint">{t('modules.pinned')}</p> : null}
      <div class="rows">
        {ORDER.map((m) => (
          <div class="row" key={m} style="display:flex;gap:16px;align-items:flex-start;padding:12px 0">
            <Switch
              on={draft.includes(m)}
              disabled={view.pinned || busy}
              label={t(`modules.${m}`)}
              onChange={(v) => flip(m, v)}
            />
            <div style="flex:1">
              <b>{t(`modules.${m}`)}</b> <Tag tone={view.enabled.includes(m) ? 'ok' : ''} dot>{t(view.enabled.includes(m) ? 'modules.on' : 'modules.off')}</Tag>
              <p class="s" style="margin:4px 0 0">{t(`modules.${m}Help`)}</p>
            </div>
          </div>
        ))}
      </div>
      {error ? (
        <p class="hint" role="alert" style="color:var(--s-bad)">
          {error}
        </p>
      ) : null}
      {view.pinned ? null : asking ? (
        <div class="banner" role="alertdialog" aria-label={t('modules.confirmTitle')}>
          <span class="bt">
            <b>{t('modules.confirmTitle')}</b>
            {off.length ? <span class="s"> {t('modules.confirmOff', { names: names(off) })}</span> : null}
            {on.length ? <span class="s"> {t('modules.confirmOn', { names: names(on) })}</span> : null}
          </span>
          <span class="x">
            <Btn sm kind="ghost" onClick={() => setAsking(false)}>
              {t('common.cancel')}
            </Btn>
            <Btn sm kind="pri" check busy={busy ? t('common.saving') : false} onClick={(e) => void save(e)}>
              {t('modules.confirm')}
            </Btn>
          </span>
        </div>
      ) : (
        <p style="margin-top:12px">
          <Btn kind="pri" disabled={!changed} fx="confirm" onClick={() => setAsking(true)}>
            {t('common.save')}
          </Btn>
        </p>
      )}
      <p class="hint" style="margin-top:12px">
        {t('modules.purgeHint')}
      </p>
    </Panel>
  )
}

/** In place of a page whose module is off: what happened and, for an owner, where to turn it on. */
export function ModuleOff({ module }: { module: EvokaiModule }) {
  const c = useConsole()
  setTitle(t('modules.offTitle'))
  const other: EvokaiModule = module === 'applications' ? 'gateway' : 'applications'
  return (
    <Empty
      action={
        c.me.deploymentOwner ? (
          <Btn kind="pri" href="#/modules">
            {t('modules.open')}
          </Btn>
        ) : moduleOn(c.me, other) ? (
          <Btn href={other === 'gateway' ? '#/gateways' : '#/apps'}>{t(`modules.${other}`)}</Btn>
        ) : null
      }
    >
      <b>{t('modules.offTitle')}</b>
      <br />
      {t('modules.offBody', { module: t(`modules.${module}`) })}
    </Empty>
  )
}
