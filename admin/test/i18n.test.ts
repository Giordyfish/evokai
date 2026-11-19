/**
 * The console's two languages stay in step: every key in both, the same placeholders, no em dash, and no text a person
 * reads written straight into a page (it belongs in src/i18n so it exists in English and Italian).
 */
import ts from 'typescript'

import { en } from '../src/i18n/en'
import { it as italian } from '../src/i18n/it'
import { dictionaries, fromBrowser, locale, rich, setLocale, t, tn } from '../src/i18n'
import { ago, day, pl, sharePct } from '../src/format'

type Msg = string | { one: string; other: string }
const EN = en as Record<string, Msg>
const IT = italian as Record<string, Msg>
const forms = (m: Msg): string[] => (typeof m === 'string' ? [m] : [m.one, m.other])
const holes = (s: string): string[] => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]!).sort()

afterEach(() => setLocale('en'))

describe('dictionaries', () => {
  it('have the same keys in English and Italian', () => {
    const missingInIt = Object.keys(EN).filter((k) => !(k in IT))
    const missingInEn = Object.keys(IT).filter((k) => !(k in EN))
    expect(missingInIt).toEqual([])
    expect(missingInEn).toEqual([])
  })

  it('have the same shape and placeholders for every key', () => {
    const wrong: string[] = []
    for (const [k, m] of Object.entries(EN)) {
      const i = IT[k]!
      if (typeof m !== typeof i) wrong.push(`${k}: plural in one language only`)
      else {
        const a = forms(m)
        const b = forms(i)
        a.forEach((s, n) => {
          if (holes(s).join() !== holes(b[n]!).join()) wrong.push(`${k}: {${holes(s)}} vs {${holes(b[n]!)}}`)
        })
      }
    }
    expect(wrong).toEqual([])
  })

  it('never contain an em dash, nor English quotes in Italian', () => {
    const all = [...Object.values(EN), ...Object.values(IT)].flatMap(forms)
    expect(all.filter((s) => s.includes(String.fromCharCode(0x2014)))).toEqual([])
    expect(Object.values(IT).flatMap(forms).filter((s) => /[“”]/.test(s))).toEqual([])
  })

  it('are the ones the console uses', () => {
    expect(dictionaries.en).toBe(en)
    expect(dictionaries.it).toBe(italian)
  })
})

describe('the language', () => {
  it('follows the browser: any it* tag is Italian, the rest English', () => {
    expect(fromBrowser(['it-IT', 'en'])).toBe('it')
    expect(fromBrowser(['it'])).toBe('it')
    expect(fromBrowser(['en-US', 'it'])).toBe('en')
    expect(fromBrowser(['de'])).toBe('en')
    expect(fromBrowser([])).toBe('en')
    expect(fromBrowser(undefined)).toBe('en')
  })

  it('switches every text, number and date', () => {
    setLocale('it')
    expect(locale()).toBe('it')
    expect(t('tab.perms')).toBe('Permessi')
    expect(t('tab.skills')).toBe('Skills')
    expect(pl(3, 'n.agent')).toBe('3 agenti')
    expect(tn('n.agent', 1)).toBe('1 agente')
    expect(sharePct(0.134)).toBe('0,13%')
    expect(sharePct(null)).toBe('nessun tetto')
    expect(ago(Date.now() - 120_000)).toBe('2 min fa')
    expect(day(new Date(new Date().getFullYear(), 10, 12))).toBe('12 nov')
    setLocale('en')
    expect(t('tab.perms')).toBe('Permissions')
    expect(pl(1_234, 'n.agent')).toBe('1,234 agents')
    expect(day(new Date(new Date().getFullYear(), 10, 12))).toBe('12 Nov')
  })

  it('fills placeholders with text and with elements', () => {
    expect(t('apps.open', { name: 'Inkwell Books' })).toBe('Open Inkwell Books')
    expect(rich('shell.readOnly', { app: 'X' })).toEqual(['Read only: your role on ', 'X', ' is viewer. Changes are refused by the service.'])
  })
})

/**
 * Text a person reads, written in a page instead of the dictionaries: JSX text, the text attributes (title, label,
 * placeholder, hint, sub, alt, aria-label, what, busy) and the first argument of toast, setError and confirm.
 * Terms of art and symbols that read the same in both languages are allowed.
 */
const SOURCES = import.meta.glob('../src/**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const SKIP = new Set(['pages/States.tsx']) // development only, never built into the console
const TEXT_ATTRS = new Set(['title', 'label', 'placeholder', 'hint', 'sub', 'alt', 'aria-label', 'what', 'busy'])
const CALLS = new Set(['toast', 'setError', 'confirm', 'setProblems'])
const SAME_IN_BOTH = /^(MCP|OWASP|JSON|JSON Schema|CSV|YAML|SKILL\.md|Skills|Evokai|Evokai Console|evokai|Python|TM|E|OP|AD|EN|IT|evk_|tools\/list|Streamable HTTP|v|ms)$/
/** Inside these elements text is code, a key or a shortcut, never prose. */
const CODE = new Set(['code', 'kbd', 'pre'])

const words = (s: string): boolean => /[A-Za-z]{2,}/.test(s) && !SAME_IN_BOTH.test(s.replace(/^[\s,.;:()]+|[\s,.;:()]+$/g, ''))
const inCode = (n: ts.Node): boolean => ts.isJsxElement(n.parent) && CODE.has(n.parent.openingElement.tagName.getText())

function hardCoded(path: string, src: string): string[] {
  const file = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const found: string[] = []
  const at = (n: ts.Node, s: string) => found.push(`${path}:${file.getLineAndCharacterOfPosition(n.getStart()).line + 1} ${JSON.stringify(s.trim())}`)
  const visit = (n: ts.Node) => {
    if (ts.isJsxText(n) && words(n.text) && !inCode(n)) at(n, n.text)
    if (ts.isJsxAttribute(n) && TEXT_ATTRS.has(n.name.getText()) && n.initializer) {
      const v = n.initializer
      const lit = ts.isStringLiteral(v) ? v : ts.isJsxExpression(v) && v.expression && ts.isStringLiteral(v.expression) ? v.expression : null
      // A placeholder without spaces is an example value (`/orders/42`, a model id), the same in both languages.
      const example = n.name.getText() === 'placeholder' && lit && !/\s/.test(lit.text)
      if (lit && words(lit.text) && !example) at(n, lit.text)
    }
    // Prose in a JSX expression (`{busy ? 'Saving the key' : ''}`) that no function call translates.
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && /[A-Za-z]{2,}\s+[A-Za-z]{2,}/.test(n.text)) {
      for (let p: ts.Node = n.parent; p && !ts.isSourceFile(p); p = p.parent) {
        if (ts.isCallExpression(p) || ts.isJsxAttribute(p)) break
        if (ts.isJsxExpression(p)) {
          if (ts.isJsxElement(p.parent) || ts.isJsxFragment(p.parent)) at(n, n.text)
          break
        }
      }
    }
    if (ts.isCallExpression(n) && CALLS.has(n.expression.getText())) {
      const a = n.arguments[0]
      if (a && (ts.isStringLiteral(a) || ts.isNoSubstitutionTemplateLiteral(a) || ts.isTemplateExpression(a)) && words(a.getText())) at(n, a.getText())
    }
    ts.forEachChild(n, visit)
  }
  visit(file)
  return found
}

it('pages carry no hard-coded text: add it to src/i18n/en.ts and it.ts', () => {
  const pages = Object.entries(SOURCES).map(([p, src]) => [p.replace('../src/', ''), src] as const)
  expect(pages.length).toBeGreaterThan(20)
  const found = pages.filter(([p]) => !SKIP.has(p)).flatMap(([p, src]) => hardCoded(p, src))
  expect(found).toEqual([])
})
