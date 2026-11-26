import { defaultFormat } from '../src/context'
import { blocks, safeHref } from '../src/markdown'
import { fill, it as itStrings, stringsFor } from '../src/strings'
import { CSS } from '../src/styles'
import { orbRgb, resolveTheme, themes, themeVars } from '../src/theme'
import { backText, pctText, renewsText, ringShare } from '../src/usage'

describe('markdown', () => {
  test('blocks: headings, lists, code, quotes, rules and tables', () => {
    const md = '## Title\n\nOne **two**\nthree\n\n- a\n- b\n\n1. x\n2. y\n\n```ts\nconst a = 1\n```\n\n> said\n\n---\n\n| A | B |\n|---|--:|\n| 1 | 2 |\n'
    expect(blocks(md).map((b) => b.t)).toEqual(['h', 'p', 'ul', 'ol', 'code', 'quote', 'hr', 'table'])
    const table = blocks(md).at(-1) as { head: string[]; align: unknown[]; rows: string[][] }
    expect(table.head).toEqual(['A', 'B'])
    expect(table.align).toEqual([null, 'right'])
    expect(table.rows).toEqual([['1', '2']])
  })

  test('only relative, http(s) and mailto links are followed', () => {
    expect(safeHref('/orders/1042')).toBe('/orders/1042')
    expect(safeHref('https://example.com')).toBe('https://example.com')
    expect(safeHref('mailto:a@b.c')).toBe('mailto:a@b.c')
    expect(safeHref('javascript:alert(1)')).toBeNull()
    expect(safeHref('//evil.example')).toBeNull()
    expect(safeHref('data:text/html,x')).toBeNull()
  })
})

describe('texts and theme', () => {
  test('locales fall back to English and take overrides', () => {
    expect(stringsFor('it').send).toBe(itStrings.send)
    expect(stringsFor('fr').send).toBe('Send')
    expect(stringsFor('en', { send: 'Go' }).send).toBe('Go')
    expect(fill('Ask {title}…', { title: 'Ada' })).toBe('Ask Ada…')
    expect(fill('{missing}', {})).toBe('{missing}')
  })

  test('no brand wording in the default texts', () => {
    for (const s of [stringsFor('en'), stringsFor('it')])
      for (const v of Object.values(s)) if (typeof v === 'string') expect(v).not.toMatch(/inkwell|bookshop/i)
  })

  test('a theme becomes custom properties; presets resolve by name', () => {
    const vars = themeVars({ light: { brand: '#111111', series: ['#1', '#2', '#3', '#4', '#5'] }, dark: { brand: '#eeeeee' }, radius: '8px', orb: { main: '#00ff00' } })
    expect(vars['--evokai-brand-light']).toBe('#111111')
    expect(vars['--evokai-brand-dark']).toBe('#eeeeee')
    expect(vars['--evokai-series-3-light']).toBe('#3')
    expect(vars['--evokai-radius']).toBe('8px')
    expect(vars['--evokai-orb-main']).toBe('#00ff00')
    expect(resolveTheme('bookshop')).toBe(themes.evokai)
    expect(resolveTheme('nope')).toBe(themes.evokai)
    expect(orbRgb({ ink: '#ff0000' }).ink).toEqual([1, 0, 0])
    expect(orbRgb({ ink: 'red' }).ink).toEqual([0.5, 0.5, 0.5])
  })

  test('the stylesheet reads every public property with a default', () => {
    expect(CSS).toContain('--ek-brand:var(--evokai-brand,var(--evokai-brand-light,')
    expect(CSS).toContain(':host([scheme=dark])')
    expect(CSS).toContain('prefers-reduced-motion')
    // Balanced parentheses in the token declarations.
    for (const decl of CSS.match(/--ek-[a-z0-9-]+:var\([^;]*;/g) ?? []) {
      expect(decl.split('(').length).toBe(decl.split(')').length)
    }
  })
})

describe('numbers and shares', () => {
  test('values are plain numbers: no currency is ever added', () => {
    expect(defaultFormat(1234.5, 'eur', 'en-US')).toBe('1,235')
    expect(defaultFormat(12.5, 'eur', 'en-US')).toBe('12.5')
    // A duration: milliseconds below a second, seconds above, in the page's language.
    expect(defaultFormat(850, 'ms', 'en-US')).toBe('850 ms')
    expect(defaultFormat(4338, 'ms', 'it-IT')).toBe('4,3 s')
    expect(defaultFormat(1234, 'eur', 'it-IT')).toBe('1234')
    expect(defaultFormat(12.25, 'pct', 'en-US')).toBe('12.3%')
    expect(defaultFormat(3, 'count', 'en-US')).toBe('3')
    expect(defaultFormat(9, 'eur', 'en-US')).not.toMatch(/[€$£]/)
  })

  test('the ring shows the day, else the month, full when a monthly cap is out', () => {
    expect(ringShare({ dayPct: 40, monthPct: 10, companyPct: 5, monthOut: false })).toBe(40)
    expect(ringShare({ dayPct: null, monthPct: 12, companyPct: 5, monthOut: false })).toBe(12)
    expect(ringShare({ dayPct: null, monthPct: null, companyPct: 7, monthOut: false })).toBe(7)
    expect(ringShare({ dayPct: 3, monthPct: 3, companyPct: 3, monthOut: true })).toBe(100)
    expect(pctText(120)).toBe('100%')
    expect(pctText(null)).toBe('0%')
  })

  test('the ring follows the binding cap and says when a reached one is back', () => {
    const binding = { limit: 'subject_week' as const, scope: 'subject' as const, period: 'week' as const, usedPct: 64, leftPct: 36, resetsAt: '2026-11-21T00:00:00Z' }
    expect(ringShare({ dayPct: 10, weekPct: 64, monthPct: null, companyPct: 5, monthOut: false, binding })).toBe(64)
    expect(ringShare({ dayPct: 10, weekPct: 100, monthPct: null, companyPct: 5, monthOut: false, out: true })).toBe(100)
    const back = backText({ dayPct: null, monthPct: null, companyPct: 1, monthOut: false, out: true, resetsAt: binding.resetsAt }, 'en-US', 'Available again {when}.')
    expect(back).toMatch(/^Available again .*November.*\.$/)
    expect(back).not.toMatch(/[€$£]/)
    expect(backText({ dayPct: null, monthPct: null, companyPct: 1, monthOut: true }, 'en-US', '{when}')).toBeNull()
  })

  test('the ring says when the share it shows renews: a weekly cap is not «every night»', () => {
    const s = { usageRenews: 'nightly', usageRenewsAt: 'at {when}' }
    const cap = (period: 'day' | 'week' | 'month', resetsAt: string) =>
      ({ limit: `subject_${period}` as const, scope: 'subject' as const, period, usedPct: 30, leftPct: 70, resetsAt })
    const u = (binding: ReturnType<typeof cap> | null) => ({ dayPct: null, weekPct: 30, monthPct: null, companyPct: 2, monthOut: false, binding })
    // In the person's own time zone, whatever the machine running the test uses.
    const local = (iso: string) =>
      new Intl.DateTimeFormat('it-IT', { weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date(iso))
    expect(renewsText(u(cap('week', '2026-11-23T00:00:00+01:00')), 'it-IT', s)).toBe(`at ${local('2026-11-23T00:00:00+01:00')}`)
    expect(renewsText(u(cap('month', '2026-12-01T00:00:00+01:00')), 'it-IT', s)).toBe(`at ${local('2026-12-01T00:00:00+01:00')}`)
    expect(renewsText(u(cap('day', '2026-11-18T00:00:00+01:00')), 'it-IT', s)).toBe('nightly')
    expect(renewsText(u(null), 'it-IT', s)).toBe('nightly')
  })
})
