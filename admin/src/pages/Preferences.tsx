/**
 * Preferences of the person signed in: their account and session, language and formats, appearance, accessibility
 * and the keyboard shortcuts. Every choice applies at once, stays in this browser for the first paint and is saved for
 * the person (their console identity) so it follows them to any browser.
 */
import type { ComponentChildren } from 'preact'
import { useEffect, useState } from 'preact/hooks'

import { describe, root } from '../api'
import { ago, clock, dateTime, day, pct1, pl } from '../format'
import { num, t, type TextKey } from '../i18n'
import { RM, toast } from '../magic'
import {
  CHOICES,
  languageOf,
  onPrefs,
  onSaveState,
  type Prefs,
  prefs,
  resetPrefs,
  saveNow,
  saveState,
  setPrefs,
  system,
} from '../prefs'
import { setTitle, useConsole } from '../state'
import { Btn, Hero, Seg, Switch, Tag } from '../ui'

function usePrefs(): Prefs {
  const [p, setP] = useState(prefs())
  useEffect(() => onPrefs(setP), [])
  return p
}

function useSaveState() {
  const [s, setS] = useState(saveState())
  useEffect(() => onSaveState(setS), [])
  return s
}

const SECTIONS: Array<[string, TextKey]> = [
  ['account', 'prefs.account'],
  ['language', 'prefs.language'],
  ['appearance', 'prefs.appearance'],
  ['a11y', 'prefs.a11y'],
  ['keys', 'prefs.keyboard'],
]

/** One setting: its name and what it does, and its control. */
function Row(p: { label: ComponentChildren; hint?: ComponentChildren; children: ComponentChildren; id?: string }) {
  return (
    <div class="item">
      <div class="t" id={p.id}>
        <b>{p.label}</b>
        {p.hint ? <small>{p.hint}</small> : null}
      </div>
      <div class="ctl">{p.children}</div>
    </div>
  )
}

function Section(p: { id: string; heading: TextKey; sub?: TextKey; children: ComponentChildren }) {
  return (
    <section class="panel flat pref" id={`pref-${p.id}`} aria-labelledby={`pref-${p.id}-h`}>
      <h3 id={`pref-${p.id}-h`}>
        {t(p.heading)}
        {p.sub ? <small>{t(p.sub)}</small> : null}
      </h3>
      {p.children}
    </section>
  )
}

function SaveStatus() {
  const s = useSaveState()
  if (s.s === 'saving') return <Tag>{t('prefs.saving')}</Tag>
  if (s.s === 'error')
    return (
      <>
        <Tag tone="bad" dot title={describe(s.error)}>
          {t('prefs.notSaved')}
        </Tag>
        <Btn kind="sm" onClick={() => void saveNow()}>
          {t('common.tryAgain')}
        </Btn>
      </>
    )
  if (s.s === 'local') return <Tag>{t('prefs.local')}</Tag>
  return (
    <Tag tone="ok" dot title={s.at ? dateTime(s.at) : undefined}>
      {t('prefs.saved')}
    </Tag>
  )
}

function Account() {
  const c = useConsole()
  const me = c.me
  const [busy, setBusy] = useState(false)
  const app = me.applicationId ? c.apps.find((a) => a.id === me.applicationId) : undefined
  const signOut = async () => {
    setBusy(true)
    try {
      await root.logout()
    } finally {
      c.signedOut()
    }
  }
  const who =
    me.kind === 'sso' ? (
      <>
        {me.name ? <b>{me.name}</b> : null}
        <span>{me.email ?? t('shell.signedIn')}</span>
      </>
    ) : me.kind === 'adm' ? (
      <>
        <b>{t('prefs.adminKey')}</b>
        <code>{me.keyPrefix ?? ''}</code>
      </>
    ) : (
      <b>{t('prefs.operatorKey')}</b>
    )
  const roles = Object.entries(me.roles ?? {})
  const expires = me.expiresAt * 1000
  return (
    <Section id="account" heading="prefs.account">
      <div class="item pref-who">
        <span class="me-av" aria-hidden="true">
          {me.kind === 'op' ? 'OP' : me.kind === 'sso' ? (me.name || me.email || '?').slice(0, 2).toUpperCase() : 'AD'}
        </span>
        <div class="t">
          {who}
          <small>{t(me.kind === 'sso' ? 'prefs.kindSso' : me.kind === 'adm' ? 'prefs.kindAdm' : 'prefs.kindOp', { app: app?.name ?? '' })}</small>
        </div>
        <div class="ctl">
          <Btn kind="sm" onClick={() => void signOut()} busy={busy ? t('prefs.signingOut') : false}>
            {t('shell.signOut')}
          </Btn>
        </div>
      </div>
      <Row label={t('prefs.roles')} hint={t(me.deploymentOwner ? 'prefs.rolesOwner' : 'prefs.rolesHint')}>
        <span class="pref-tags">
          {roles.map(([slug, role]) => (
            <Tag key={slug} tone={role === 'owner' ? 'vi' : ''}>
              {slug === '*' ? t('access.everyApp') : slug === 'own' ? (app?.name ?? t('prefs.ownApp')) : slug}: {role}
            </Tag>
          ))}
        </span>
      </Row>
      <Row
        label={t('prefs.session')}
        hint={t(me.kind === 'adm' ? 'prefs.sessionAdm' : me.kind === 'sso' ? 'prefs.sessionSso' : 'prefs.sessionOp')}
      >
        <span class="pref-val" title={dateTime(expires)}>
          {t('prefs.sessionEnds', { when: dateTime(expires), left: ago(expires) })}
        </span>
      </Row>
    </Section>
  )
}

function Language({ p }: { p: Prefs }) {
  const sys = system()
  const browserLang = languageOf({ ...p, language: 'auto' }, sys)
  const sample = new Date(new Date().getFullYear(), 10, 15, 14, 30)
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const i = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(i)
  }, [])
  // Each choice shows its own example.
  const dateLabel = (v: Prefs['dateFormat']) => day(sample, true, v)
  const timeLabel = (v: Prefs['timeFormat']) => clock(sample, v)
  return (
    <Section id="language" heading="prefs.language">
      <Row label={t('prefs.uiLanguage')} hint={t('prefs.uiLanguageHint')}>
        <Seg
          options={CHOICES.language}
          value={p.language}
          onChange={(v) => setPrefs({ language: v })}
          labels={{ auto: t('prefs.followBrowser', { lang: t(`lang.${browserLang}`) }), en: t('lang.en'), it: t('lang.it') }}
        />
      </Row>
      <Row label={t('prefs.dateFormat')} hint={t('prefs.dateFormatHint')}>
        <Seg
          options={CHOICES.dateFormat}
          value={p.dateFormat}
          onChange={(v) => setPrefs({ dateFormat: v })}
          labels={{
            auto: t('prefs.auto', { example: dateLabel('auto') }),
            dmy: dateLabel('dmy'),
            mdy: dateLabel('mdy'),
            ymd: dateLabel('ymd'),
          }}
        />
      </Row>
      <Row label={t('prefs.timeFormat')} hint={t('prefs.timeFormatHint')}>
        <Seg
          options={CHOICES.timeFormat}
          value={p.timeFormat}
          onChange={(v) => setPrefs({ timeFormat: v })}
          labels={{ auto: t('prefs.auto', { example: timeLabel('auto') }), '24h': timeLabel('24h'), '12h': timeLabel('12h') }}
        />
      </Row>
      <Row label={t('prefs.numbers')} hint={t('prefs.numbersHint')}>
        <span class="pref-val">
          {num(1234567.89)} · {pct1(12.5)}
        </span>
      </Row>
      <div class="pref-preview" role="status" aria-live="polite">
        <span>{t('prefs.preview')}</span>
        <b>{dateTime(now)}</b>
        <span class="sep">·</span>
        {t('prefs.previewLine', { day: day(now), agents: pl(3, 'n.agent'), n: num(1234.5) })}
      </div>
    </Section>
  )
}

function Appearance({ p }: { p: Prefs }) {
  return (
    <Section id="appearance" heading="prefs.appearance">
      <Row label={t('prefs.theme')} hint={t('prefs.themeHint', { now: t(system().dark ? 'prefs.dark' : 'prefs.light').toLowerCase() })}>
        <Seg
          options={CHOICES.theme}
          value={p.theme}
          onChange={(v) => setPrefs({ theme: v })}
          labels={{ system: t('prefs.followSystem'), light: t('prefs.light'), dark: t('prefs.dark') }}
        />
      </Row>
      <Row label={t('prefs.density')} hint={t('prefs.densityHint')}>
        <Seg
          options={CHOICES.density}
          value={p.density}
          onChange={(v) => setPrefs({ density: v })}
          labels={{ comfortable: t('prefs.comfortable'), compact: t('prefs.compact') }}
        />
      </Row>
    </Section>
  )
}

function Accessibility({ p }: { p: Prefs }) {
  const sys = system()
  const sysWord = (on: boolean) => t(on ? 'prefs.sysOn' : 'prefs.sysOff')
  return (
    <Section id="a11y" heading="prefs.a11y">
      <Row label={t('prefs.motion')} hint={t('prefs.motionHint', { now: sysWord(sys.reducedMotion) })}>
        <Seg
          options={CHOICES.motion}
          value={p.motion}
          onChange={(v) => setPrefs({ motion: v })}
          labels={{ system: t('prefs.followSystem'), reduce: t('prefs.on'), full: t('prefs.off') }}
        />
      </Row>
      <Row label={t('prefs.mascot')} hint={t('prefs.mascotHint')}>
        <Seg
          options={CHOICES.mascot}
          value={p.mascot}
          onChange={(v) => setPrefs({ mascot: v })}
          labels={{ on: t('prefs.mascotOn'), still: t('prefs.mascotStill'), hidden: t('prefs.mascotHidden') }}
        />
      </Row>
      <Row label={t('prefs.textSize')} hint={t('prefs.textSizeHint')}>
        <Seg
          options={CHOICES.textSize.map(String) as Array<'100' | '112' | '125'>}
          value={String(p.textSize) as '100' | '112' | '125'}
          onChange={(v) => setPrefs({ textSize: Number(v) as Prefs['textSize'] })}
          labels={{ '100': `${num(100)}%`, '112': `${num(112)}%`, '125': `${num(125)}%` }}
        />
      </Row>
      <Row label={t('prefs.contrast')} hint={t('prefs.contrastHint', { now: sysWord(sys.moreContrast) })}>
        <Seg
          options={CHOICES.contrast}
          value={p.contrast}
          onChange={(v) => setPrefs({ contrast: v })}
          labels={{ system: t('prefs.followSystem'), more: t('prefs.on'), normal: t('prefs.off') }}
        />
      </Row>
      <Row label={t('prefs.focus')} hint={t('prefs.focusHint')} id="pref-focus">
        <Switch on={p.focusAlways} onChange={(v) => setPrefs({ focusAlways: v })} aria={t('prefs.focus')} />
      </Row>
      <Row label={t('prefs.links')} hint={t('prefs.linksHint')}>
        <Switch on={p.underlineLinks} onChange={(v) => setPrefs({ underlineLinks: v })} aria={t('prefs.links')} />
      </Row>
    </Section>
  )
}

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)

function Keyboard() {
  const rows: Array<[ComponentChildren, TextKey]> = [
    [<kbd>{MAC ? '⌘ K' : 'Ctrl K'}</kbd>, 'prefs.kPalette'],
    [
      <>
        <kbd>↑</kbd> <kbd>↓</kbd>
      </>,
      'prefs.kMove',
    ],
    [<kbd>Enter</kbd>, 'prefs.kOpen'],
    [<kbd>Esc</kbd>, 'prefs.kClose'],
    [
      <>
        <kbd>Tab</kbd> <kbd>Shift Tab</kbd>
      </>,
      'prefs.kTab',
    ],
    [
      <>
        <kbd>Space</kbd> <kbd>Enter</kbd>
      </>,
      'prefs.kSwitch',
    ],
  ]
  return (
    <Section id="keys" heading="prefs.keyboard">
      <dl class="pref-keys">
        {rows.map(([k, d], i) => [
          <dt key={`k${i}`}>{k}</dt>,
          <dd key={`d${i}`}>{t(d)}</dd>,
        ])}
      </dl>
    </Section>
  )
}

export function Preferences({ q }: { q: URLSearchParams }) {
  const p = usePrefs()
  const [cur, setCur] = useState('account')
  setTitle(t('page.preferences'))
  const jump = (id: string, smooth = true) => {
    setCur(id)
    document.getElementById(`pref-${id}`)?.scrollIntoView({ block: 'start', behavior: smooth && !RM() ? 'smooth' : 'auto' })
  }
  // The section in view is the current one in the side list.
  useEffect(() => {
    const on = () => {
      let at = SECTIONS[0]![0]
      for (const [k] of SECTIONS) {
        const el = document.getElementById(`pref-${k}`)
        if (el && el.getBoundingClientRect().top < innerHeight * 0.35) at = k
      }
      if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) at = SECTIONS[SECTIONS.length - 1]![0]
      setCur(at)
    }
    addEventListener('scroll', on, { passive: true })
    return () => removeEventListener('scroll', on)
  }, [])
  // The palette opens a section directly (`#/preferences?s=a11y`).
  const asked = q.get('s')
  useEffect(() => {
    if (asked && SECTIONS.some(([k]) => k === asked)) setTimeout(() => jump(asked, false), 30)
  }, [asked])
  const reset = async () => {
    await resetPrefs()
    toast(t('prefs.resetDone'))
  }
  return (
    <>
      <Hero
        title={t('page.preferences')}
        sub={t('prefs.sub')}
        actions={
          <>
            <SaveStatus />
            <Btn kind="ghost" onClick={() => void reset()}>
              {t('prefs.reset')}
            </Btn>
          </>
        }
      />
      <div class="prefs">
        <nav class="secnav" aria-label={t('prefs.sections')}>
          {SECTIONS.map(([k, l]) => (
            <a
              key={k}
              role="button"
              tabIndex={0}
              aria-current={k === cur ? 'true' : undefined}
              onClick={() => jump(k)}
              onKeyDown={(e) => e.key === 'Enter' && jump(k)}
            >
              {t(l)}
            </a>
          ))}
        </nav>
        <div class="grid">
          <Account />
          <Language p={p} />
          <Appearance p={p} />
          <Accessibility p={p} />
          <Keyboard />
        </div>
      </div>
    </>
  )
}
