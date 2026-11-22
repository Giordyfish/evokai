/**
 * Whether the console assistant is active for the signed-in person: available in this deployment and activated, read
 * with the first application the login sees (no application: not active). While it is, its panel holds the one mascot
 * on screen and the sidebar shows the logo.
 */
import { useEffect, useState } from 'preact/hooks'

import { adminJson } from '../api'
import { useConsole } from '../state'
import { assistantActive, type AssistantState } from './choice'

/** Read again when `again` changes (the panel opens or closes, the Assistant page is entered or left). */
export function useAssistantActive(...again: unknown[]): boolean {
  const c = useConsole()
  const anyApp = c.apps[0]?.id ?? ''
  const [active, setActive] = useState(false)
  useEffect(() => {
    if (!anyApp) {
      setActive(false)
      return
    }
    let live = true
    adminJson<AssistantState>(anyApp, 'GET', '/v1/admin/assistant')
      .then((s) => live && setActive(assistantActive(s)))
      .catch(() => live && setActive(false))
    return () => {
      live = false
    }
  }, [anyApp, ...again])
  return active
}
