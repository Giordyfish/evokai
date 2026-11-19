/** Applications: each connects with its own key. An operator can create one (its keys are shown once). */
import { useEffect, useState } from 'preact/hooks'

import type { NewApplicationKeys } from '@client/core/types'
import { describe, root } from '../api'
import { bar, copy, pl, sharePct } from '../format'
import { Empty, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole } from '../state'
import { AppAvatar, Bar, Btn, Field, Hero, Icon, Panel, Tag } from '../ui'
import { storageLabel, voiceText } from './summary'
import { t } from '../i18n'

function health(status: string | null) {
  if (status === 'slow') return <Tag tone="warn" dot>{t('apps.slowServer')}</Tag>
  if (status === 'down') return <Tag tone="bad" dot>{t('apps.serverDown')}</Tag>
  if (status === 'ok') return <Tag tone="ok" dot>{t('common.healthy')}</Tag>
  return <Tag>{t('common.notChecked')}</Tag>
}

export function Apps() {
  const c = useConsole()
  const [creating, setCreating] = useState(false)
  setTitle(t('page.applications'))
  // First run: an operator with no application goes straight to the setup wizard.
  useEffect(() => {
    if (c.canCreate && !c.apps.length) go('#/setup')
  }, [c.canCreate, c.apps.length])
  const create = c.canCreate ? (
    <span style="display:flex;gap:8px">
      <Btn kind="pri" href="#/setup">
        {t('apps.newApplication')}
      </Btn>
      <Btn kind="ghost" onClick={() => setCreating(true)}>
        {t('apps.onlyKeys')}
      </Btn>
    </span>
  ) : null
  return (
    <>
      <Hero title={t('page.applications')} sub={t('apps.sub')} actions={create} />
      {creating ? <NewApp onClose={() => setCreating(false)} /> : null}
      {!c.apps.length ? (
        <Panel>
          <Empty action={create}>
            <b>{t('apps.empty')}</b>
            <br />
            {t('apps.emptyText')}
          </Empty>
        </Panel>
      ) : (
        <div class="grid g3">
          {c.apps.map((a) => {
            const used = a.usage.monthPct
            return (
              <article class="panel appc" key={a.id} tabIndex={0} onClick={() => go(appHref(a.slug))} onKeyDown={(e) => e.key === 'Enter' && go(appHref(a.slug))}>
                <div class="row">
                  <AppAvatar slug={a.slug} name={a.name} />
                  <div>
                    <h2>{a.name}</h2>
                    <span class="s" style="color:var(--mu);font-size:13px">
                      {a.slug}
                    </span>
                  </div>
                  <span style="margin-left:auto">{health(a.mcpStatus)}</span>
                </div>
                <div class="meter">
                  <b>{sharePct(used, t('apps.noCap'))}</b>
                  <span style="color:var(--mu)">
                    {used != null ? t('apps.capUsed', { left: sharePct(a.usage.monthLeftPct) }) : t('apps.noMonthlyCap')}
                  </span>
                </div>
                <Bar value={bar(used)} />
                <div class="caps">
                  <Tag tone="vi">{pl(a.counts.agents, 'n.agent')}</Tag>
                  <Tag>{pl(a.grants.tools.on, 'n.tool')}</Tag>
                  <Tag>{voiceText(a.grants)}</Tag>
                  <Tag>{storageLabel(a.grants)}</Tag>
                  <Tag tone="kserver">{pl(a.counts.overrides, 'n.override')}</Tag>
                </div>
              </article>
            )
          })}
          {c.canCreate ? (
            <button class="newc" onClick={() => go('#/setup')}>
              <div>
                <Icon name="plus" />
                <div style="font-weight:550">{t('apps.newApplication')}</div>
                <div style="font-size:13px">{t('apps.newCardSub')}</div>
              </div>
            </button>
          ) : null}
        </div>
      )}
    </>
  )
}

function NewApp({ onClose }: { onClose: () => void }) {
  const c = useConsole()
  const [slug, setSlug] = useState('')
  const [name, setName] = useState('')
  const [origins, setOrigins] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [keys, setKeys] = useState<NewApplicationKeys | null>(null)
  const submit = async (e: Event) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const k = await root.operator.applications.create({
        slug: slug.trim(),
        name: name.trim(),
        allowed_origins: origins.split(/\s+/).filter(Boolean),
      })
      setKeys(k)
      sparkle(e.target as Element)
      toast(t('apps.created'), t('apps.copyNow'))
      await c.reloadApps()
    } catch (err) {
      setError(describe(err))
    } finally {
      setBusy(false)
    }
  }
  if (keys)
    return (
      <Panel title={<>{t('apps.newKeys')} <Tag tone="gold" dot>{t('apps.shownOnce')}</Tag></>} cls="keyc on">
        {(
          [
            [t('apps.serverKey'), keys.serverKey],
            [t('apps.adminKey'), keys.adminKey],
          ] as const
        ).map(([label, k]) => (
          <div key={label}>
            <span class="lbl">{label}</span>
            <div class="keyrow">
              <div class="k shim">{k}</div>
              <Btn sm onClick={async () => toast((await copy(k)) ? t('apps.keyCopied', { label }) : t('common.copyByHand'), t('apps.notShownAgain'), null)}>
                {t('common.copy')}
              </Btn>
            </div>
          </div>
        ))}
        <div style="display:flex;gap:8px;margin-top:6px">
          <Btn kind="pri" sm href={appHref(slug.trim())}>
            {t('apps.open', { name: name.trim() })}
          </Btn>
          <Btn sm kind="ghost" onClick={onClose}>
            {t('common.close')}
          </Btn>
        </div>
      </Panel>
    )
  return (
    <Panel title={t('apps.newApplication')} style="margin-bottom:16px">
      <form onSubmit={submit}>
        <div class="grid g3" style="gap:0 14px">
          <Field label={t('common.name')} id="na-name">
            <input id="na-name" class="in" value={name} onInput={(e) => setName(e.currentTarget.value)} required maxLength={120} />
          </Field>
          <Field label={t('apps.slug')} id="na-slug" hint={t('apps.slugHint')}>
            <input id="na-slug" class="in mono" value={slug} onInput={(e) => setSlug(e.currentTarget.value)} required pattern="[a-z0-9][a-z0-9-]{1,40}" />
          </Field>
          <Field label={t('apps.origins')} id="na-origins" hint={t('apps.originsHint')}>
            <input id="na-origins" class="in mono" value={origins} onInput={(e) => setOrigins(e.currentTarget.value)} placeholder="https://app.example" />
          </Field>
        </div>
        {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:10px">{error}</p> : null}
        <div style="display:flex;gap:8px">
          <Btn kind="pri" type="submit" busy={busy ? t('apps.creating') : false}>
            {t('apps.create')}
          </Btn>
          <Btn kind="ghost" onClick={onClose}>
            {t('common.cancel')}
          </Btn>
        </div>
      </form>
    </Panel>
  )
}
