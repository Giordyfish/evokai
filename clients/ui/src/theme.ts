/**
 * Theming: every colour, font and radius is a CSS custom property (`--evokai-*`), so a page can theme the components
 * from its own stylesheet; a `Theme` object sets the same properties from code, with a light and a dark palette and the
 * orb's colours. For deeper changes see `skin` (your CSS inside the components), `unstyled`, parts, slots and `icons`.
 */
import type { ChatState } from './chatstate'

/** Colours of one scheme. Any CSS colour. */
export interface Palette {
  /** Page-coloured surfaces (the panel). */
  surface: string
  /** Muted surfaces (the composer, cards). */
  muted: string
  /** Main text. */
  text: string
  /** Secondary text. */
  subtle: string
  /** Hairlines. */
  border: string
  /** The brand colour: buttons, the user's bubbles, the selected state. */
  brand: string
  /** Text on the brand colour. */
  onBrand: string
  /** A lighter accent: focus rings, live dots, positive deltas. */
  accent: string
  /** Errors and refusals. */
  danger: string
  /** The voice stage's veil over the page. */
  veil: string
  /** Chart series, the main one first. */
  series: [string, string, string, string, string]
}

/** The orb's four colours, from deep to bright. */
export interface OrbColors {
  ink: string
  main: string
  light: string
  glow: string
}

/** A chat state (`chatstate.ts`): each may have its own image. */
export type OrbState = ChatState

/**
 * An orb made of images: one per chat state (a still image, an animated GIF, WebP or PNG), `idle` for the states left
 * out. `still` is shown instead with reduced motion (without it, the first frame of the state's image). A transition
 * (`"confirm>tool"`, or `"*>done"` from any state) may play a one-shot image for `ms` before the state's own.
 */
export type OrbImages = { idle: string; still?: string; transitions?: Record<string, { src: string; ms: number }> } & Partial<Record<Exclude<OrbState, 'idle'>, string>>

export interface Theme {
  /** `light`, `dark`, or `auto` (follows the system; default). */
  scheme?: 'light' | 'dark' | 'auto'
  light?: Partial<Palette>
  dark?: Partial<Palette>
  /** Body text font stack. */
  font?: string
  /** Headings and the voice stage's subtitles. */
  displayFont?: string
  /** Corner radius of the panel (cards and bubbles follow). */
  radius?: string
  /** A logo (image URL) shown in the panel header instead of the small orb. */
  logo?: string
  /** The orb's colours, and `images` to draw it with images instead (see `OrbImages`). */
  orb?: Partial<OrbColors> & { images?: OrbImages }
  /** The launcher's corner: `bottom-right` (default) or `bottom-left`. */
  position?: 'bottom-right' | 'bottom-left'
}

export const LIGHT: Palette = {
  surface: '#ffffff',
  muted: '#f4f3f8',
  text: '#16131f',
  subtle: '#6b6880',
  border: '#e4e2ec',
  brand: '#2a1a4a',
  onBrand: '#f5f3ff',
  accent: '#8b5cf6',
  danger: '#c0262d',
  veil: 'rgb(246 245 250 / 0.74)',
  series: ['#6d28d9', '#c4b5fd', '#0f766e', '#d97706', '#be185d'],
}

export const DARK: Palette = {
  surface: '#14111c',
  muted: '#1e1a29',
  text: '#f3f1f8',
  subtle: '#a6a1b8',
  border: '#2e2940',
  brand: '#c4b5fd',
  onBrand: '#1b1030',
  accent: '#a78bfa',
  danger: '#f87171',
  veil: 'rgb(10 8 16 / 0.8)',
  series: ['#a78bfa', '#5b4b8a', '#2dd4bf', '#fbbf24', '#f472b6'],
}

export const ORB: OrbColors = { ink: '#1e0b3d', main: '#7c3aed', light: '#c4b5fd', glow: '#f5f3ff' }

/** Presets by name: the default look. A brand theme is a `Theme` object of the application (or its stylesheet). */
export const themes: Record<'evokai', Theme> = {
  evokai: {},
}

const kebab = (s: string) => s.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)

function paletteVars(p: Partial<Palette>, suffix: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(p)) {
    if (k === 'series' && Array.isArray(v)) v.forEach((c, i) => (out[`--evokai-series-${i + 1}${suffix}`] = c))
    else if (typeof v === 'string') out[`--evokai-${kebab(k)}${suffix}`] = v
  }
  return out
}

/**
 * The custom properties of a theme, to set on the host element. Light colours are `--evokai-<name>-light`, dark ones
 * `--evokai-<name>-dark`; the stylesheet picks one by the scheme. Unset keys keep their defaults.
 */
export function themeVars(theme: Theme | null | undefined): Record<string, string> {
  if (!theme) return {}
  const out: Record<string, string> = {
    ...paletteVars(theme.light ?? {}, '-light'),
    ...paletteVars(theme.dark ?? {}, '-dark'),
  }
  if (theme.font) out['--evokai-font'] = theme.font
  if (theme.displayFont) out['--evokai-display-font'] = theme.displayFont
  if (theme.radius) out['--evokai-radius'] = theme.radius
  for (const [k, v] of Object.entries(theme.orb ?? {})) if (typeof v === 'string' && v) out[`--evokai-orb-${k}`] = v
  return out
}

/** The orb's colours of a theme (without its images). */
export function orbColors(orb: Theme['orb']): Partial<OrbColors> | undefined {
  if (!orb) return undefined
  const { images: _images, ...colors } = orb
  return colors
}

/** The images of an orb made of images: the `orb-images` attribute (JSON) wins over the theme's; null without `idle`. */
export function orbImages(attr: string | null, orb: Theme['orb']): OrbImages | null {
  const ok = (v: unknown): v is OrbImages => !!v && typeof v === 'object' && typeof (v as OrbImages).idle === 'string'
  if (attr) {
    try {
      const v: unknown = JSON.parse(attr)
      if (ok(v)) return v
    } catch {
      // Not JSON: the theme's images, if any.
    }
  }
  return ok(orb?.images) ? orb.images : null
}

/** The image for a state (idle when the state has none). */
export function orbImageFor(images: OrbImages, state: OrbState): string {
  const own = state === 'idle' ? undefined : images[state]
  return typeof own === 'string' && own ? own : images.idle
}

/** The one-shot image of a change of state, if the images have one. */
export function orbTransition(images: OrbImages, from: OrbState, to: OrbState): { src: string; ms: number } | null {
  const t = images.transitions?.[`${from}>${to}`] ?? images.transitions?.[`*>${to}`]
  return t && typeof t.src === 'string' && t.ms > 0 ? t : null
}

/** A preset by name, or the theme itself. */
export function resolveTheme(theme: Theme | keyof typeof themes | string | null | undefined): Theme {
  if (!theme) return themes.evokai
  if (typeof theme === 'string') return (themes as Record<string, Theme>)[theme] ?? themes.evokai
  return theme
}

/** The orb's colours as 0–1 RGB triples (for the shader), from CSS hex colours. */
export function orbRgb(colors: Partial<OrbColors> | undefined): Record<keyof OrbColors, [number, number, number]> {
  const c = { ...ORB, ...colors }
  const rgb = (hex: string): [number, number, number] => {
    const h = hex.trim().replace('#', '')
    const full = h.length === 3 ? h.split('').map((x) => x + x).join('') : h.slice(0, 6)
    const n = Number.parseInt(full, 16)
    if (!/^[0-9a-f]{6}$/i.test(full) || Number.isNaN(n)) return [0.5, 0.5, 0.5]
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]
  }
  return { ink: rgb(c.ink), main: rgb(c.main), light: rgb(c.light), glow: rgb(c.glow) }
}

/** The orb's colours from the host's computed style (custom properties), falling back to the theme. */
export function orbFromStyle(el: Element | null, fallback: Partial<OrbColors> | undefined): Partial<OrbColors> {
  const out: Partial<OrbColors> = { ...fallback }
  if (!el || typeof getComputedStyle !== 'function') return out
  const cs = getComputedStyle(el)
  for (const k of ['ink', 'main', 'light', 'glow'] as const) {
    const v = cs.getPropertyValue(`--evokai-orb-${k}`).trim()
    if (v.startsWith('#')) out[k] = v
  }
  return out
}
