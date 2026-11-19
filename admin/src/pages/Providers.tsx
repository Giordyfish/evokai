/**
 * Providers: the provider catalog (who processes data, where, under whose control, what the API terms let them keep),
 * every fact with the official page it was read from. No prices: the console never shows amounts.
 */
import type { ConsoleApplication, ProviderCatalogView, ProviderEntry, ProviderEndpointView } from '@client/core/types'
import { forApp } from '../api'
import { setTitle, useLoad } from '../state'
import { Hero, KV, Panel, Tag } from '../ui'
import { Loads } from './common'
import { num, t, tn, type TextKey } from '../i18n'

export function Providers({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.providers'), app.name)
  const load = useLoad(() => forApp(app.id).admin.providers.catalog(), [app.id])
  return (
    <>
      <Hero title={t('tab.providers')} sub={t('prov.sub')} />
      <Loads load={load} what={t('what.providers')}>
        {(c) => <Catalog c={c} />}
      </Loads>
    </>
  )
}

function Catalog({ c }: { c: ProviderCatalogView }) {
  const cov = c.coverage
  return (
    <>
      <Panel title={t('prov.coverage')}>
        <KV
          rows={[
            [t('prov.version'), `${c.version} · ${c.checked ?? ''}`],
            [t('prov.providers'), num(cov.providers)],
            [t('prov.endpoints'), num(cov.endpoints)],
            [t('prov.processors'), num(cov.processors)],
            [t('prov.known'), num(cov.known)],
            [t('prov.unknown'), num(cov.unknown)],
          ]}
        />
        <p class="hint">{t('prov.coverageHint')}</p>
      </Panel>
      <Panel title={t('prov.list')}>
        {c.providers.map((p) => (
          <ProviderItem key={p.id} p={p} />
        ))}
      </Panel>
      <Panel title={t('prov.processorsTitle')}>
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('prov.processor')}</th>
                <th>{t('prov.kind')}</th>
                <th>{t('prov.where')}</th>
                <th>{t('prov.controlledFrom')}</th>
              </tr>
            </thead>
            <tbody>
              {c.processors.map((x) => (
                <tr key={x.id}>
                  <td>
                    <b>{x.title}</b>
                    <div class="s">
                      <code>{x.id}</code>
                      {x.detects ? <> · {t(`prov.detects.${x.detects}` as TextKey)}</> : null}
                    </div>
                  </td>
                  <td>{t(`dp.kind.${x.kind}` as TextKey)}</td>
                  <td>{where(x.facts)}</td>
                  <td>{x.facts?.controlledFrom ?? (x.facts?.inProcess ? t('dp.inProcess') : t('dp.unknown'))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  )
}

function ProviderItem({ p }: { p: ProviderEntry }) {
  return (
    <details class="prov">
      <summary>
        <b>{p.title}</b> <code>{[p.id, ...p.aliases].map((x) => `${x}:`).join(' ')}</code>{' '}
        <Tag tone={p.entity.controlledFrom ? '' : 'warn'}>{t('prov.controlledBy', { who: p.entity.controlledFrom ?? t('dp.unknown') })}</Tag>{' '}
        {p.selfHosted ? <Tag>{t('prov.selfHosted')}</Tag> : null}
      </summary>
      <KV
        rows={[
          [t('prov.entity'), p.entity.name ?? t('dp.unknown')],
          [t('prov.euEntity'), p.entity.euContractingEntity ?? t('dp.unknown')],
          [t('prov.terms'), terms(p.terms)],
          [t('prov.zdr'), p.terms.zeroRetention ? t(`prov.zdr.${p.terms.zeroRetention}` as TextKey) : t('dp.unknown')],
          [t('prov.caching'), p.caching.style ? t(`prov.cache.${p.caching.style}` as TextKey) : t('dp.unknown')],
        ]}
      />
      {p.terms.notes ? <p class="s">{p.terms.notes}</p> : null}
      {p.endpoints.length ? (
        <div class="wx">
          <table class="tbl">
            <thead>
              <tr>
                <th>{t('prov.endpoint')}</th>
                <th>{t('prov.where')}</th>
                <th>{t('prov.models')}</th>
              </tr>
            </thead>
            <tbody>
              {p.endpoints.map((e) => (
                <EndpointRow key={e.id} e={e} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {p.unknowns.length ? (
        <>
          <p class="hint">{tn('prov.unknowns', p.unknowns.length)}</p>
          <ul class="s">
            {p.unknowns.map((u) => (
              <li key={u}>{u}</li>
            ))}
          </ul>
        </>
      ) : null}
      <Sources urls={[...p.entity.sources, ...p.terms.sources, ...p.caching.sources]} />
    </details>
  )
}

function EndpointRow({ e }: { e: ProviderEndpointView }) {
  return (
    <tr>
      <td>
        <code>{e.id}</code>
        <div class="s">{e.kind ?? ''}</div>
        {e.declaredBy === 'operator' ? <Tag>{t('prov.declared')}</Tag> : null}
      </td>
      <td>{e.fromDeploymentRegion ? t('prov.deploymentRegion', { region: (e.regions ?? []).join(', ') }) : where(e)}</td>
      <td>
        <div class="s mono" style="overflow-wrap:anywhere">
          {e.models.join(' ')}
        </div>
        <Sources urls={e.sources} />
      </td>
    </tr>
  )
}

function Sources({ urls }: { urls: string[] }) {
  const unique = [...new Set(urls)].filter((u) => u.startsWith('https://'))
  if (!unique.length) return null
  return (
    <div class="s">
      {t('prov.sources')}{' '}
      {unique.map((u, i) => (
        <a key={u} href={u} target="_blank" rel="noopener noreferrer">
          {t('prov.sourceN', { n: i + 1 })}
        </a>
      ))}
    </div>
  )
}

/** Where processing happens, as the catalog says it. */
export function where(f: { geography?: string | null; countries?: string[] | null; inProcess?: boolean } | null | undefined): string {
  if (!f) return t('dp.unknown')
  if (f.inProcess) return t('dp.inProcess')
  const parts = [f.geography ? f.geography.toUpperCase() : '', (f.countries ?? []).join(', ')].filter(Boolean)
  return parts.length ? parts.join(' · ') : t('dp.unknown')
}

function terms(x: { training: boolean | null; retention: string | null; retentionDays: number | null }): string {
  const kept = x.retention ? t(`prov.ret.${x.retention}` as TextKey, { days: x.retentionDays ?? '' }) : t('dp.unknown')
  const trained = x.training == null ? t('dp.unknown') : x.training ? t('prov.trains') : t('prov.noTraining')
  return `${kept} · ${trained}`
}
