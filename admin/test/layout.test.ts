// Vitest hands a CSS import back empty: the file is read as it is (Node, in the test only).
interface NodeFs {
  readFileSync(path: URL, encoding: 'utf8'): string
}
const node = (globalThis as unknown as { process: { getBuiltinModule(id: string): NodeFs } }).process
const css = node.getBuiltinModule('node:fs').readFileSync(new URL('../src/styles.css', import.meta.url), 'utf8')

/** The rules of the stylesheet's first `@media (max-width:900px)` block (the narrow shell). */
function narrowShell(): string {
  const start = css.indexOf('@media (max-width:900px){')
  let depth = 0
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++
    if (css[i] === '}' && --depth === 0) return css.slice(start, i + 1)
  }
  return ''
}

describe('the narrow shell', () => {
  it('turns the sidebar into a drawer', () => {
    expect(narrowShell()).toContain('.shell.open .side{transform:none}')
  })
  it('hides the assistant launcher while the drawer is open, so it never covers the drawer', () => {
    // The launcher sits above everything (its own z-index); on a phone it fell on the drawer's foot.
    expect(narrowShell()).toContain('body:has(.shell.open) evokai-chat{visibility:hidden}')
  })
})
