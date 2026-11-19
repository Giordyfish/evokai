/**
 * The EN / IT choice on the sign-in page, where nobody is signed in yet. It sets the language preference (kept in this
 * browser, and carried to the person's preferences on sign in). Signed in, the language is chosen in Preferences.
 */
import { LOCALES, locale, t } from './i18n'
import { setPrefs } from './prefs'

export function LangSwitch() {
  const cur = locale()
  return (
    <span class="lang" role="group" aria-label={t('lang.label')}>
      {LOCALES.map((l) => (
        <button key={l} type="button" lang={l} aria-pressed={l === cur} title={t(`lang.${l}`)} onClick={() => setPrefs({ language: l })}>
          {l.toUpperCase()}
        </button>
      ))}
    </span>
  )
}
