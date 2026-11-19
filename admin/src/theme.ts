/** Light and dark: the theme preference (`src/prefs.ts`) sets `data-theme` on <html>; this is the quick toggle. */
import { useEffect, useState } from 'preact/hooks'

import { onPrefs, setPrefs } from './prefs'

export const isDark = (): boolean => document.documentElement.dataset.theme === 'dark'

/** Flip between light and dark (an explicit choice, kept in the person's preferences). */
export function toggleTheme(): 'light' | 'dark' {
  const t = isDark() ? 'light' : 'dark'
  setPrefs({ theme: t })
  return t
}

/** Whether the console is dark now, following the preferences and the system. */
export function useDark(): boolean {
  const [dark, setDark] = useState(isDark())
  useEffect(() => onPrefs(() => setDark(isDark())), [])
  return dark
}
