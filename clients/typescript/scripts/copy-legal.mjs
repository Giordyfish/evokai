// npm packs only files inside the package: copy the repository's LICENSE and NOTICE next to package.json.
import { copyFileSync } from 'node:fs'

for (const name of ['LICENSE', 'NOTICE']) copyFileSync(new URL(`../../../${name}`, import.meta.url), new URL(`../${name}`, import.meta.url))
