/**
 * What the console decides about the assistant's launcher, with no drawing: how the chat looks in the console, which
 * login opens its session and what it is told about the page.
 */
import { ORB_MAX } from '@ui/travel'

/**
 * The chat's look inside the console: the mascot a little larger than the orb in the launcher and in the header (it is
 * a figure, not a sphere), drawn at its full size in the middle of voice mode. The launcher and the panel stay at the
 * bottom right, as in the applications. Until the first-use notice is read (`data-notice` on the host) voice cannot start from
 * the launcher, and while the notice is open the launcher steps aside.
 */
export const SKIN = `
.oslot.launch{min-width:76px;min-height:76px}
.oslot.head{width:84px;height:84px;margin-right:0;overflow:visible}
.oslot.mid{width:${ORB_MAX}px;height:${ORB_MAX}px}
:host([data-notice]) [part~="launcher-actions"]{display:none}
:host([data-notice=open]) :is(.dock,[part~="orb"]){opacity:0;pointer-events:none}
`

/**
 * The application whose console login opens the assistant's session. The assistant is the platform's: its tools name
 * the application of each call, so this only has to be one the person can see (the first that is not its own).
 */
export function sessionApp<A extends { id: string }>(apps: A[], systemId: string | undefined): A | null {
  return apps.find((a) => a.id !== systemId) ?? apps[0] ?? null
}

/** What the assistant is told about the page the person is on: its console path and title, never its text. */
export function pageContext(hash: string, title: string): { path: string; title: string } {
  return { path: hash.replace(/^#/, '') || '/', title }
}
