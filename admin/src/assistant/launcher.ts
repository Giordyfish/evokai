/**
 * What the console decides about the assistant's launcher, with no drawing: how the chat looks in the console and
 * which application the assistant acts on.
 */
import { ORB_MAX } from '@ui/travel'

/** Where the launcher and the panel sit on a wide screen: past the sidebar (12 + 268 px), so its footer stays usable. */
export const SIDE_OFFSET = 292

/**
 * The chat's look inside the console: the mascot a little larger than the orb in the launcher and in the header (it is
 * a figure, not a sphere), drawn at its full size in the middle of voice mode; on a wide screen the launcher and the
 * panel start past the sidebar. Until the first-use notice is read (`data-notice` on the host) voice cannot start from
 * the launcher, and while the notice is open the launcher steps aside.
 */
export const SKIN = `
.oslot.launch{min-width:76px;min-height:76px}
.oslot.head{width:84px;height:84px;margin-right:0;overflow:visible}
.oslot.mid{width:${ORB_MAX}px;height:${ORB_MAX}px}
@media (min-width:901px){.dock,.panel{left:${SIDE_OFFSET}px}}
:host([data-notice]) [part~="launcher-actions"]{display:none}
:host([data-notice=open]) :is(.dock,[part~="orb"]){opacity:0;pointer-events:none}
`

/**
 * The application the assistant acts on: the one the person is looking at, else the last one they opened, else the
 * first one they can see that is not the assistant's own.
 */
export function targetApp<A extends { id: string }>(selected: A | null, last: string | null, apps: A[], systemId: string | undefined): A | null {
  if (selected) return selected
  return apps.find((a) => a.id === last) ?? apps.find((a) => a.id !== systemId) ?? apps[0] ?? null
}
