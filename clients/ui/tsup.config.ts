import { defineConfig } from 'tsup'

// Preact is bundled (the components render with it inside their shadow roots); the headless client is a peer
// dependency, so an application that already uses it loads it once.
export default defineConfig({
  entry: { index: 'src/index.ts', react: 'src/react.ts' },
  format: ['esm'],
  target: 'es2022',
  platform: 'browser',
  dts: true,
  sourcemap: true,
  clean: true,
  splitting: true,
  treeshake: true,
  minify: true,
  external: ['@evokai/evokai-client', '@evokai/evokai-client/browser', 'react', 'react/jsx-runtime'],
  noExternal: ['preact'],
  esbuildOptions(o) {
    o.jsx = 'automatic'
    o.jsxImportSource = 'preact'
  },
})
