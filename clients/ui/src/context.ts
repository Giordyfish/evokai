/** What every component reads: texts, number formatting and labels. */
import { createContext } from 'preact'
import { useContext } from 'preact/hooks'

import type { Unit } from '@evokai/evokai-client/browser'
import { en, type Strings } from './strings'

/**
 * Formats a chart value. The default writes plain numbers in the page's language, `pct` with a percent sign; units
 * are the application's own (e.g. `eur` for its business figures): pass a formatter to show them your way.
 */
export type ValueFormatter = (value: number, unit: Unit, locale: string) => string

export const defaultFormat: ValueFormatter = (value, unit, locale) => {
  const nf = new Intl.NumberFormat(locale, { maximumFractionDigits: unit === 'count' || unit === 'pct' ? 1 : Math.abs(value) < 100 && value % 1 ? 2 : 0 })
  return unit === 'pct' ? `${nf.format(value)}%` : nf.format(value)
}

export interface UiContext {
  s: Strings
  /** BCP 47 language of numbers and dates. */
  lang: string
  fmt: ValueFormatter
  /** A label for a tool's activity chip (the tool name when absent). */
  toolLabel: (name: string) => string
  /** `{title}` and `{app}` for the texts. */
  names: { title: string; app: string }
  /** Replaced icons, by name: SVG markup (a whole `<svg>` or its content in a 24 by 24 box). */
  icons?: Partial<Record<string, string>> | undefined
}

export const Ui = createContext<UiContext>({
  s: en,
  lang: 'en-US',
  fmt: defaultFormat,
  toolLabel: (n) => n,
  names: { title: 'Assistant', app: '' },
})

export const useUi = (): UiContext => useContext(Ui)
