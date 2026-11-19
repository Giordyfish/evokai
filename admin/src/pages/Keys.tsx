/** Keys: only hashes are stored. To rotate, create the successor, deploy it, then revoke the old key. */
import { useEffect, useState } from 'preact/hooks'

import type { ApiKeyInfo, ConsoleApplication } from '@client/core/types'
import { describe, forApp } from '../api'
import { ago, copy, day } from '../format'
import { Empty, Fx, RM, sparkle, toast } from '../magic'
import { setTitle, useConsole, useLoad } from '../state'
import { Btn, Hero, Panel, Tag } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'

interface Shown {
  kind: 'srv' | 'adm'
  key: string
  replaces?: ApiKeyInfo
  n: number
}

function KeySlip({ shown, onRevokeOld }: { shown: Shown; onRevokeOld: (k: ApiKeyInfo) => void }) {
  const [burnt, setBurnt] = useState(RM())
  useEffect(() => {
    if (RM()) return
    setBurnt(false)
    const timer = setTimeout(() => setBurnt(true), 2900)
    return () => clearTimeout(timer)
  }, [shown.n])
  return (
    <Panel cls="keyc on">
      <h3 style="font:600 15.5px var(--disp)">
        {t(shown.kind === 'srv' ? 'keys.newServer' : 'keys.newAdmin')}{' '}
        <Tag tone="gold" dot>
          {t('apps.shownOnce')}
        </Tag>
      </h3>
      <div class="keyrow">
        <div class="k shim" key={shown.n}>
          {shown.key}
        </div>
        <span class="kfx">{burnt ? <Fx k="flameLoop" size={56} /> : <Fx k="flame" size={72} once />}</span>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <Btn
          kind="pri"
          sm
          check
          onClick={async () => toast((await copy(shown.key)) ? t('keys.copied') : t('common.copyByHand'), t('apps.notShownAgain'), null)}
        >
          {t('keys.copyKey')}
        </Btn>
        {shown.replaces ? (
          <Btn sm kind="ghost danger" onClick={() => onRevokeOld(shown.replaces!)}>
            {t('keys.revokeOld', { prefix: shown.replaces.prefix })}
          </Btn>
        ) : null}
        <span class="hint" style="margin:0">
          {shown.replaces ? t('keys.deployFirst') : t('keys.hashOnly')}
        </span>
      </div>
    </Panel>
  )
}

export function Keys({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.keys'), app.name)
  const c = useConsole()
  const api = forApp(app.id)
  const load = useLoad(() => api.admin.keys.list(), [app.id])
  const [shown, setShown] = useState<Shown | null>(null)
  const [arming, setArming] = useState<string | null>(null)

  const create = async (kind: 'srv' | 'adm', e: Event) => {
    const btn = e.currentTarget as Element
    try {
      const r = await api.admin.keys.create({ kind, label: kind === 'srv' ? 'server key' : 'admin key' })
      setShown({ kind, key: r.key, n: Date.now() })
      sparkle(btn)
      toast(t(kind === 'srv' ? 'keys.serverCreated' : 'keys.adminCreated'), t('keys.copyNow'), null)
      await load.reload()
      await c.reloadApps()
    } catch (err) {
      toast(t('keys.notCreated'), describe(err), null)
    }
  }
  const rotate = async (k: ApiKeyInfo) => {
    try {
      const r = await api.admin.keys.rotate(k.id)
      setShown({ kind: k.kind, key: r.key, replaces: k, n: Date.now() })
      toast(t('keys.successor'), t('keys.deployThenRevoke', { prefix: k.prefix }), null)
      await load.reload()
      await c.reloadApps()
    } catch (err) {
      toast(t('keys.notRotated'), describe(err), null)
    }
  }
  const revoke = async (k: ApiKeyInfo) => {
    if (arming !== k.id) {
      setArming(k.id)
      setTimeout(() => setArming((x) => (x === k.id ? null : x)), 3000)
      return
    }
    setArming(null)
    try {
      await api.admin.keys.revoke(k.id)
      toast(t('keys.revoked'), k.prefix, null)
      if (shown?.replaces?.id === k.id) setShown({ ...shown, replaces: undefined })
      await load.reload()
      await c.reloadApps()
    } catch (err) {
      toast(t('keys.notRevoked'), describe(err), null)
    }
  }

  return (
    <>
      <Hero
        title={t('tab.keys')}
        sub={t('keys.sub')}
        actions={
          <>
            <Btn fx="key" onClick={(e) => void create('adm', e)}>
              {t('keys.createAdmin')}
            </Btn>
            <Btn kind="pri" fx="key" onClick={(e) => void create('srv', e)}>
              {t('keys.createServer')}
            </Btn>
          </>
        }
      />
      {shown ? <KeySlip shown={shown} onRevokeOld={(k) => void revoke(k)} /> : null}
      <Loads load={load} what={t('what.keys')}>
        {(keys) =>
          keys.length ? (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('keys.key')}</th>
                      <th>{t('keys.kind')}</th>
                      <th>{t('keys.created')}</th>
                      <th>{t('keys.lastUsed')}</th>
                      <th>{t('keys.state')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {keys.map((k) => (
                      <tr key={k.id}>
                        <td>
                          <code>evk_{k.kind}_{k.prefix}_••••</code>
                          <div class="s">{k.current ? t('keys.thisSession') : k.label}</div>
                        </td>
                        <td>
                          <Tag tone={k.kind === 'srv' ? 'vi' : 'kserver'}>{k.kind === 'srv' ? 'server' : 'admin'}</Tag>
                        </td>
                        <td class="s">{day(k.createdAt)}</td>
                        <td class="s">{k.current ? t('ago.now') : ago(k.lastUsedAt)}</td>
                        <td>
                          {k.revoked ? (
                            <Tag tone="bad" dot>
                              {t('keys.revokedTag')}
                            </Tag>
                          ) : (
                            <Tag tone="ok" dot>
                              {t('keys.active')}
                            </Tag>
                          )}
                        </td>
                        <td class="r" style="white-space:nowrap">
                          {!k.revoked ? (
                            <>
                              <Btn sm kind="ghost" onClick={() => void rotate(k)}>
                                {t('keys.rotate')}
                              </Btn>
                              {!k.current ? (
                                <Btn sm kind="ghost danger" onClick={() => void revoke(k)}>
                                  {arming === k.id ? t('keys.confirmRevoke') : t('keys.revoke')}
                                </Btn>
                              ) : null}
                            </>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          ) : (
            <Panel>
              <Empty k="ember">
                <b>{t('keys.empty')}</b>
                <br />
                {t('keys.emptyText', { app: app.name })}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}
