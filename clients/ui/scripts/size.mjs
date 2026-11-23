// The bundle size budget: what a page downloads for each entry point (its own code and Preact, gzipped; the headless
// client is a peer dependency and is reported apart). Fails the build when an entry grows past its budget.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

const dist = new URL('../dist/', import.meta.url)
const client = new URL('../../typescript/dist/browser/', import.meta.url)
// +0.5 KB in November 2026: the conversation's state on the host (data-state, statechange) for slotted content.
// +0.5 KB in November 2026: a custom orb (a slotted element or images per state) travelling in place of the orb.
// +0.5 KB in November 2026: client tools, your own tool cards (toolRenderers) and regenerating the last answer.
// +1.5 KB in November 2026: attachments (the paper clip, drop and paste, previews, the files of each question, EN+IT).
const BUDGET_KB = { 'index.js': 49.5, 'react.js': 50 }
const CLIENT_BUDGET_KB = 60

const gz = (url) => gzipSync(readFileSync(url)).length / 1024

/** An entry and every chunk it imports, transitively (static imports only). */
function closure(dir, file, seen = new Set()) {
  const url = new URL(file, dir).href
  if (seen.has(url)) return seen
  seen.add(url)
  const text = readFileSync(new URL(url), 'utf8')
  for (const m of text.matchAll(/(?:import|export)\s*(?:[^'"]*?from\s*)?["'](\.\.?\/[^"']+\.js)["']/g)) closure(new URL('.', url), m[1], seen)
  return seen
}

let failed = false
for (const [entry, budget] of Object.entries(BUDGET_KB)) {
  const files = [...closure(dist, entry)]
  const kb = files.reduce((t, f) => t + gz(new URL(f)), 0)
  const ok = kb <= budget
  failed ||= !ok
  console.log(`${ok ? 'ok  ' : 'OVER'} ${entry.padEnd(9)} ${kb.toFixed(1)} KB gzip (budget ${budget} KB, ${files.length} files)`)
}
try {
  statSync(new URL('index.js', client))
  const files = [...closure(client, 'index.js')]
  const kb = files.reduce((t, f) => t + gz(new URL(f)), 0)
  const ok = kb <= CLIENT_BUDGET_KB
  failed ||= !ok
  console.log(`${ok ? 'ok  ' : 'OVER'} client    ${kb.toFixed(1)} KB gzip (@evokai/evokai-client/browser, budget ${CLIENT_BUDGET_KB} KB)`)
} catch {
  console.log('     client    not built (clients/typescript)')
}
for (const f of readdirSync(dist)) if (f.endsWith('.js') && !f.startsWith('chunk-') && !(f in BUDGET_KB)) console.log(`     ${f} (not budgeted)`)
if (failed) {
  console.error('bundle size budget exceeded')
  process.exit(1)
}
