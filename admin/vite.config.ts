// The console builds to admin/dist, served by the service at {EVOKAI_BASE_PATH}/admin/. Paths are relative, so the same
// build works under any base path. It uses the headless client's sources directly (no published package needed).
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

const client = fileURLToPath(new URL('../clients/typescript/src', import.meta.url))

export default defineConfig({
  base: './',
  resolve: { alias: { '@client': client } },
  esbuild: { jsx: 'automatic', jsxImportSource: 'preact' },
  // Nothing inlined as data: URIs: the console's CSP allows fonts and scripts from its own origin only.
  build: { outDir: 'dist', emptyOutDir: true, assetsDir: 'assets', assetsInlineLimit: 0, sourcemap: false, target: 'es2022' },
  server: {
    port: 5173,
    fs: { allow: ['..'] },
    // `npm run dev` against a local service: EVOKAI_URL=http://localhost:8787 (cookies stay same-origin through the proxy).
    proxy: { '^/[^/]+/v1/': { target: process.env.EVOKAI_URL ?? 'http://localhost:8787', changeOrigin: false } },
  },
  test: { globals: true, environment: 'node', include: ['test/**/*.test.ts'], setupFiles: ['test/english.ts'] },
})
