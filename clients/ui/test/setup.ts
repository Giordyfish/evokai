// jsdom has no canvas: the orbs fall back to their CSS gradient, as in a browser without WebGL.
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as typeof HTMLCanvasElement.prototype.getContext

// Node 25 and newer define their own localStorage and sessionStorage, empty without --localstorage-file, and they
// shadow jsdom's: use jsdom's, as a browser would.
const dom = (globalThis as { jsdom?: { window: Window } }).jsdom
for (const key of ['localStorage', 'sessionStorage'] as const) {
  if (dom && !globalThis[key]) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
}
