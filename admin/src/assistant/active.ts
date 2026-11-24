/**
 * Whether the console assistant is active for the signed-in person: available in this deployment and activated, read
 * with the first application the login sees (no application: not active). While it is, the mascot is its launcher at
 * the bottom right (the one mascot on screen) and the sidebar shows the logo.
 */
import { useEffect, useState } from 'preact/hooks'

import { adminJson } from '../api'
import { useConsole } from '../state'
import { assistantActive, type AssistantState } from './choice'

/** The assistant's state (null until read, or when it cannot be read); read again when `again` changes. */
export function useAssistantState(...again: unknown[]): AssistantState | null {
  const c = useConsole()
  const anyApp = c.apps[0]?.id ?? ''
  const [state, setState] = useState<AssistantState | null>(null)
  useEffect(() => {
    if (!anyApp) {
      setState(null)
      return
    }
    let live = true
    adminJson<AssistantState>(anyApp, 'GET', '/v1/admin/assistant')
      .then((s) => live && setState(s))
      .catch(() => live && setState(null))
    return () => {
      live = false
    }
  }, [anyApp, ...again])
  return state
}

/** Read again when `again` changes (the Assistant page is entered or left). */
export function useAssistantActive(...again: unknown[]): boolean {
  return assistantActive(useAssistantState(...again))
}

