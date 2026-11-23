/** The line icons of the panel and the stage; each can be replaced (`icons` on the element). */
import type { VNode } from 'preact'

import { useUi } from './context'

const I = {
  history: <path d="M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 7v5l3 2" />,
  plus: <path d="M12 5v14M5 12h14" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </>
  ),
  wave: <path d="M3 10v4M7 7v10M11 4v16M15 8v8M19 10v4" />,
  up: <path d="M12 19V5M5 12l7-7 7 7" />,
  stop: <rect x="7" y="7" width="10" height="10" rx="1.5" />,
  thumbUp: <path d="M7 10v11M3 11h4v10H3zM7 11l4-8a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 2H7" />,
  thumbDown: <path d="M17 14V3M21 13h-4V3h4zM17 13l-4 8a2 2 0 0 1-3-2l1-5H5a2 2 0 0 1-2-2l2-7a2 2 0 0 1 2-2h10" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" stroke-width="3" />,
  back: <path d="M19 12H5M11 18l-6-6 6-6" />,
  memory: <path d="M12 3a7 7 0 0 0-4 12.7V18a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 3zM9.5 22h5" />,
  edit: <path d="M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16v4zM13.5 6.5l4 4" />,
  again: <path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5" />,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" />,
  // One family: an arrow into the orb's corner (voice goes on in the launcher) and out of it (back to the panel).
  minimize: (
    <>
      <path d="M4 4l9 9M13 7v6H7" />
      <circle cx="18.5" cy="18.5" r="2.6" fill="currentColor" stroke="none" />
    </>
  ),
  restore: (
    <>
      <path d="M13 13 4 4M4 10V4h6" />
      <circle cx="18.5" cy="18.5" r="2.6" fill="currentColor" stroke="none" />
    </>
  ),
  chart: <path d="M3 3v18h18M7 16v-3M12 16V8M17 16v-6" />,
  clip: <path d="M20 11l-8 8a5 5 0 0 1-7-7l8.5-8.5a3.5 3.5 0 0 1 5 5L10 17a2 2 0 0 1-3-3l7.5-7.5" />,
}

/** The names of the replaceable icons. */
export type IconName = keyof typeof I | 'keyboard'

export const ICON_NAMES: IconName[] = [...(Object.keys(I) as IconName[]), 'keyboard']

const KEYBOARD = (
  <>
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 16h8" />
  </>
)

export function Icon({ d, size = 16 }: { d: IconName; size?: number }): VNode {
  const custom = useUi().icons?.[d]
  if (custom) {
    // Your own SVG: a whole <svg> element or only its content (drawn in a 24 by 24 box, stroked with currentColor).
    const whole = /^\s*<svg[\s>]/i.test(custom)
    return whole ? (
      <span class="icon" data-icon={d} style={{ display: 'inline-grid', width: `${size}px`, height: `${size}px` }} aria-hidden="true" dangerouslySetInnerHTML={{ __html: custom }} />
    ) : (
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" data-icon={d} dangerouslySetInnerHTML={{ __html: custom }} />
    )
  }
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      {d === 'keyboard' ? KEYBOARD : I[d]}
    </svg>
  )
}
