import { defineConfig } from 'tsup'

export default defineConfig({
  entry: {
    'server/index': 'src/server/index.ts',
    'browser/index': 'src/browser/index.ts',
    'react/index': 'src/react/index.ts',
  },
  format: ['esm'],
  target: 'es2022',
  platform: 'neutral',
  dts: true,
  sourcemap: true,
  clean: true,
  splitting: true,
  treeshake: true,
  external: ['react', 'react-dom', 'react/jsx-runtime', '@pipecat-ai/client-js', '@pipecat-ai/small-webrtc-transport'],
})
