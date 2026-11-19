// Copies the sound files next to every bundle that references them, so `new URL('./sounds/…', import.meta.url)`
// resolves in dist/ exactly as it does in src/ (bundlers then emit them as assets).
import { copyFileSync, mkdirSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const sounds = join(root, 'src/browser/audio/sounds')
const files = readdirSync(sounds)

function walk(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? walk(p) : [p]
  })
}

let copied = 0
for (const file of walk(join(root, 'dist')).filter((f) => f.endsWith('.js'))) {
  if (!readFileSync(file, 'utf8').includes('./sounds/classico-on.mp3')) continue
  const to = join(dirname(file), 'sounds')
  mkdirSync(to, { recursive: true })
  for (const f of files) copyFileSync(join(sounds, f), join(to, f))
  copied++
}
if (!copied) throw new Error('copy-assets: no bundle references the sounds')
