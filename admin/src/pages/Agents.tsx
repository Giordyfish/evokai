/** The agents of an application: a conversation keeps the version it started with. */
import type { ConsoleApplication } from '@client/core/types'
import { forApp } from '../api'
import { modelName } from '../format'
import { Empty } from '../magic'
import { appHref, go, setTitle, useLoad } from '../state'
import { Btn, Hero, Panel } from '../ui'
import { Loads } from './common'
import { t } from '../i18n'
import { pl } from '../format'
import { ChannelTags } from './Overview'

export function Agents({ app }: { app: ConsoleApplication }) {
  setTitle(t('tab.agents'), app.name)
  const api = forApp(app.id)
  const load = useLoad(async () => {
    const [agents, usage] = await Promise.all([api.admin.agents.list(), api.usage({ groupBy: 'agent' })])
    return { agents, calls: new Map(usage.rows.map((r) => [r.agent ?? '', r.calls])) }
  }, [app.id])
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'agents', 'new')}>
      {t('agents.new')}
    </Btn>
  )
  return (
    <>
      <Hero title={t('tab.agents')} sub={t('agents.sub')} actions={add} />
      <Loads load={load} what={t('what.agents')}>
        {({ agents, calls }) =>
          !agents.length ? (
            <Panel>
              <Empty action={add}>
                <b>{t('ov.noAgents')}</b>
                <br />
                {t('agents.emptyText', { app: app.name })}
              </Empty>
            </Panel>
          ) : (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('agents.agent')}</th>
                      <th>{t('agents.channels')}</th>
                      <th>{t('agents.chain')}</th>
                      <th>MCP</th>
                      <th>Skills</th>
                      <th class="r">{t('agents.live')}</th>
                      <th class="r">{t('agents.thisMonth')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {agents.map((g) => (
                      <tr key={g.name} data-go onClick={() => go(appHref(app.slug, 'agents', g.name))}>
                        <td>
                          <b>{g.name}</b>
                          <div class="s">{g.title}</div>
                        </td>
                        <td>
                          <ChannelTags channels={g.channels ?? ['browser']} />
                        </td>
                        <td class="s">{[g.model, ...(g.fallbacks ?? [])].map(modelName).join(t('common.then'))}</td>
                        <td>
                          <code>{(g.mcp_servers ?? []).join(', ') || t('common.none')}</code>
                        </td>
                        <td class="num" style="text-align:left">
                          {g.skills == null ? t('agents.allSkills') : g.skills.length}
                        </td>
                        <td class="num">{g.version.slice(0, 7)}</td>
                        <td class="num">{pl(calls.get(g.name) ?? 0, 'n.call')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}
