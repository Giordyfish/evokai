/** Skills: folders with a SKILL.md, loaded only when an agent needs one. */
import { useRef, useState } from 'preact/hooks'

import type { AgentDef, ConsoleApplication, SkillDetail, SkillFile, SkillFileInfo } from '@client/core/types'
import { describe, forApp } from '../api'
import { bytes, day } from '../format'
import { done, Empty, sparkle, toast } from '../magic'
import { appHref, go, setTitle, useConsole, useLoad } from '../state'
import { Btn, Field, Hero, KV, Panel, Tag } from '../ui'
import { Loads } from './common'
import { num, t } from '../i18n'

/** SKILL.md: front matter (name, description) and the body. */
export function parseSkill(text: string): { name: string; description: string; body: string } | null {
  const m = /^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/.exec(text.replace(/\r\n/g, '\n'))
  if (!m) return null
  const meta: Record<string, string> = {}
  for (const line of m[1]!.split('\n')) {
    const i = line.indexOf(':')
    if (i > 0) meta[line.slice(0, i).trim()] = line.slice(i + 1).trim().replace(/^["']|["']$/g, '')
  }
  if (!meta.name || !meta.description) return null
  return { name: meta.name, description: meta.description, body: m[2]!.trim() }
}

export const skillText = (s: { name: string; description: string; body: string }): string =>
  `---\nname: ${s.name}\ndescription: ${s.description}\n---\n${s.body}\n`

const usedBy = (agents: AgentDef[], name: string) => agents.filter((a) => a.skills == null || a.skills.includes(name)).map((a) => a.name)

export function Skills({ app, id }: { app: ConsoleApplication; id: string | null }) {
  setTitle(id ? (id === 'new' ? t('skills.new') : id) : t('tab.skills'), app.name)
  const api = forApp(app.id)
  const list = useLoad(async () => {
    const [skills, agents] = await Promise.all([api.admin.skills.list(), api.admin.agents.list()])
    return { skills, agents }
  }, [app.id])
  if (id) return <Detail key={id} app={app} id={id} agents={list.data?.agents ?? []} />
  const add = (
    <Btn kind="pri" href={appHref(app.slug, 'skills', 'new')}>
      {t('skills.add')}
    </Btn>
  )
  return (
    <>
      <Hero title={t('tab.skills')} sub={t('skills.sub')} actions={add} />
      <Loads load={list} what={t('what.skills')}>
        {({ skills, agents }) =>
          skills.length ? (
            <Panel cls="flat">
              <div class="wx">
                <table class="tbl">
                  <thead>
                    <tr>
                      <th>{t('skills.skill')}</th>
                      <th>{t('skills.description')}</th>
                      <th>{t('skills.agents')}</th>
                      <th class="r">{t('skills.files')}</th>
                      <th class="r">{t('skills.size')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {skills.map((s) => {
                      const by = usedBy(agents, s.name)
                      return (
                        <tr key={s.name} data-go onClick={() => go(appHref(app.slug, 'skills', s.name))}>
                          <td>
                            <code>{s.name}</code>
                          </td>
                          <td class="s" style="max-width:420px">
                            {s.description}
                          </td>
                          <td>{by.length ? by.map((x) => <Tag key={x} tone="vi">{x}</Tag>) : <Tag>{t('skills.unused')}</Tag>}</td>
                          <td class="num">{num(s.files ?? 0)}</td>
                          <td class="num">{bytes(s.size)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          ) : (
            <Panel>
              <Empty action={add}>
                <b>{t('skills.empty')}</b>
                <br />
                {t('skills.emptyText')}
              </Empty>
            </Panel>
          )
        }
      </Loads>
    </>
  )
}

function Detail({ app, id, agents }: { app: ConsoleApplication; id: string; agents: AgentDef[] }) {
  const c = useConsole()
  const api = forApp(app.id)
  const isNew = id === 'new'
  const load = useLoad<SkillDetail | null>(() => (isNew ? Promise.resolve(null) : api.admin.skills.get(id)), [app.id, id])
  const [text, setText] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <Loads load={load} what={isNew ? t('what.newSkill') : id}>
      {(s) => {
        const value = text ?? (s ? skillText(s) : skillText({ name: 'new-skill', description: t('skills.tplDescription'), body: t('skills.tplBody') }))
        const save = async (e: Event) => {
          const btn = e.currentTarget as Element
          const parsed = parseSkill(value)
          if (!parsed) {
            setError(t('skills.needsFront'))
            return
          }
          if (!isNew && parsed.name !== id) {
            setError(t('skills.nameFixed'))
            return
          }
          setSaving(true)
          setError(null)
          try {
            await api.admin.skills.put(parsed.name, { description: parsed.description, body: parsed.body })
            sparkle(btn)
            done(btn)
            toast(t('skills.saved'), parsed.name)
            await c.reloadApps()
            if (isNew) go(appHref(app.slug, 'skills', parsed.name))
            else await load.reload()
          } catch (err) {
            setError(describe(err))
          } finally {
            setSaving(false)
          }
        }
        const remove = async () => {
          if (!confirm(t('skills.confirmRemove', { name: id }))) return
          try {
            await api.admin.skills.delete(id)
            toast(t('skills.removed'), id, null)
            await c.reloadApps()
            go(appHref(app.slug, 'skills'))
          } catch (err) {
            toast(t('skills.notRemoved'), describe(err), null)
          }
        }
        const by = usedBy(agents, s?.name ?? '')
        return (
          <>
            <Hero
              title={isNew ? t('skills.new') : id}
              sub={s?.description ?? t('skills.newSub')}
              actions={
                <Btn kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void save(e)}>
                  {t('common.save')}
                </Btn>
              }
            />
            {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
            <div class="grid g-21">
              <Panel title="SKILL.md">
                <textarea class="in" style="min-height:300px" spellcheck={false} value={value} onInput={(e) => setText(e.currentTarget.value)} />
              </Panel>
              <Panel title={t('skills.details')}>
                <KV
                  rows={[
                    [t('skills.enabledBy'), by.join(', ') || t('common.none')],
                    [t('skills.size'), s ? bytes(s.size) : t('skills.sizeNew')],
                    [t('skills.updated'), s ? day(s.updatedAt) : t('skills.notSaved')],
                  ]}
                />
                <p class="hint" style="margin-top:12px">
                  {t('skills.promptNote')}
                </p>
                {s ? (
                  <p class="hint">
                    {t('skills.limits', {
                      description: s.limits.descriptionChars,
                      body: s.limits.bodyChars,
                      total: bytes(s.limits.skillBytes),
                      files: s.limits.files,
                      script: bytes(s.limits.scriptBytes),
                      read: s.limits.readChars,
                    })}
                  </p>
                ) : null}
                {!isNew ? (
                  <Btn sm kind="ghost danger" onClick={() => void remove()}>
                    {t('skills.remove')}
                  </Btn>
                ) : null}
              </Panel>
            </div>
            {s ? <Files app={app} skill={s} reload={load.reload} /> : <Panel title={t('skills.files')}><p class="hint">{t('skills.filesAfterSave')}</p></Panel>}
          </>
        )
      }}
    </Loads>
  )
}

const KIND_TONE = { script: 'gold', data: 'teal', reference: '' } as const

/** The files of a skill: the list (kind, size, title), a viewer, add or replace, remove. */
function Files({ app, skill, reload }: { app: ConsoleApplication; skill: SkillDetail; reload: () => Promise<unknown> }) {
  const api = forApp(app.id)
  const [open, setOpen] = useState<SkillFile | null>(null)
  const [path, setPath] = useState('')
  const [content, setContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const scripts = skill.scripts
  const hasScripts = skill.files.some((f) => f.kind === 'script')
  const view = async (f: SkillFileInfo) => {
    if (open?.path === f.path) return setOpen(null)
    try {
      setOpen(await api.admin.skills.files.get(skill.name, f.path))
    } catch (err) {
      toast(t('skills.files'), describe(err), null)
    }
  }
  const remove = async (f: SkillFileInfo) => {
    if (!confirm(t('skills.confirmRemoveFile', { path: f.path, name: skill.name }))) return
    try {
      await api.admin.skills.files.delete(skill.name, f.path)
      toast(t('skills.fileRemoved'), f.path, null)
      if (open?.path === f.path) setOpen(null)
      await reload()
    } catch (err) {
      toast(t('skills.notRemoved'), describe(err), null)
    }
  }
  const add = async (e: Event) => {
    const btn = e.currentTarget as Element
    const p = path.trim()
    if (!p) return setError(t('skills.needsPath'))
    setSaving(true)
    setError(null)
    try {
      await api.admin.skills.files.put(skill.name, p, content)
      sparkle(btn)
      done(btn)
      toast(t('skills.fileSaved'), p)
      setPath('')
      setContent('')
      await reload()
    } catch (err) {
      setError(describe(err))
    } finally {
      setSaving(false)
    }
  }
  const pick = async (list: FileList | null) => {
    const f = list?.[0]
    if (!f) return
    setContent(await f.text())
    if (!path.trim()) setPath(f.name.endsWith('.py') ? `scripts/${f.name}` : `references/${f.name}`)
  }
  const runnable = (f: SkillFileInfo) =>
    f.kind !== 'script' ? null : scripts.runnable ? (
      <Tag tone="ok">{t('skills.runnable')}</Tag>
    ) : (
      <Tag tone="warn" title={t(`skills.scriptsOff.${scripts.reason ?? 'agent'}`)}>
        {t('skills.notRunnable')}
      </Tag>
    )
  return (
    <>
      <Panel title={t('skills.files')}>
        <p class="hint" style="margin-top:0">
          {t('skills.filesSub')}
        </p>
        {hasScripts ? (
          <p class="hint" style={scripts.runnable ? '' : 'color:var(--s-warn)'}>
            {scripts.runnable ? t('skills.scriptsOn', { agents: scripts.agents.join(', ') }) : t(`skills.scriptsOff.${scripts.reason ?? 'agent'}`)}
          </p>
        ) : null}
        {skill.files.length ? (
          <div class="wx">
            <table class="tbl">
              <thead>
                <tr>
                  <th>{t('skills.path')}</th>
                  <th>{t('skills.kind')}</th>
                  <th>{t('skills.title')}</th>
                  <th class="r">{t('skills.size')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {skill.files.map((f) => (
                  <tr key={f.path}>
                    <td>
                      <code>{f.path}</code>
                    </td>
                    <td>
                      <Tag tone={KIND_TONE[f.kind]}>{t(`skills.kind.${f.kind}`)}</Tag> {runnable(f)}
                    </td>
                    <td class="s">{f.title}</td>
                    <td class="num">{t('skills.chars', { n: f.size })}</td>
                    <td class="r">
                      <Btn sm kind="ghost" onClick={() => void view(f)}>
                        {open?.path === f.path ? t('skills.hide') : t('skills.view')}
                      </Btn>
                      <Btn sm kind="ghost danger" onClick={() => void remove(f)}>
                        {t('skills.removeFile')}
                      </Btn>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p class="hint">{t('skills.filesEmpty')}</p>
        )}
        {open ? (
          <Field label={open.path} id="sk-view">
            <textarea id="sk-view" class="in" style="min-height:220px" spellcheck={false} readOnly value={open.content} />
          </Field>
        ) : null}
      </Panel>
      <Panel title={t('skills.addFileTitle')}>
        {error ? <p class="hint" style="color:var(--s-bad);margin-bottom:12px">{error}</p> : null}
        <Field label={t('skills.path')} id="sk-path" hint={t('skills.pathHint')}>
          <input id="sk-path" class="in" value={path} spellcheck={false} maxLength={200} onInput={(e) => setPath(e.currentTarget.value)} />
        </Field>
        <Field label={t('skills.content')} id="sk-content">
          <textarea id="sk-content" class="in" style="min-height:160px" spellcheck={false} value={content} onInput={(e) => setContent(e.currentTarget.value)} />
        </Field>
        <Btn sm onClick={() => picker.current?.click()}>
          {t('skills.chooseFile')}
        </Btn>{' '}
        <input
          ref={picker}
          type="file"
          hidden
          accept=".md,.txt,.csv,.tsv,.json,.yaml,.yml,.py"
          onChange={(e) => {
            const el = e.currentTarget
            void pick(el.files).then(() => {
              el.value = ''
            })
          }}
        />
        <Btn sm kind="pri" check busy={saving ? t('common.saving') : false} onClick={(e) => void add(e)}>
          {t('skills.addFile')}
        </Btn>
      </Panel>
    </>
  )
}
