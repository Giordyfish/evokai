/**
 * The console's guided tours, as data. A tour is an id and its steps; a step is a console route (a hash path, where
 * `{app}` is the current application's slug), the place it points at (an element marked `data-tour="..."` on that
 * page, or `tour="..."` on a Panel or Btn) and an optional side for the card. Its texts are in the dictionaries:
 * `tour.<tour>.title`, `tour.<tour>.sub` and, per step, `tour.<tour>.<step>.title` and `tour.<tour>.<step>.body`
 * (at most two short lines).
 *
 * `test/tours.test.ts` checks that every step's place exists in the source of the page it routes to, and that every
 * text exists in English and Italian.
 */

export type Placement = 'auto' | 'top' | 'bottom' | 'left' | 'right'

/** What a step needs to make sense: an application to show, the right to create one, owning the deployment. */
export type Need = 'app' | 'create' | 'owner'

export interface TourStep {
  id: string
  /** A console route: `/apps`, `/apps/{app}/security`, `/playground?app={app}`. */
  route: string
  /** The `data-tour` value of the element the step points at. */
  target: string
  placement?: Placement
  /** Beyond the application its route implies (`{app}`). */
  needs?: Need[]
}

export interface Tour {
  id: string
  steps: TourStep[]
}

export const TOURS: Tour[] = [
  {
    id: 'first-steps',
    steps: [
      { id: 'apps', route: '/apps', target: 'apps-list' },
      { id: 'new-app', route: '/apps', target: 'apps-new', needs: ['create'] },
      { id: 'setup', route: '/setup', target: 'setup-steps', needs: ['create'] },
      { id: 'agents', route: '/apps/{app}/agents', target: 'agents-list' },
      { id: 'new-agent', route: '/apps/{app}/agents', target: 'agents-new' },
      { id: 'agent', route: '/apps/{app}/agents/new', target: 'agent-sections', placement: 'right' },
      { id: 'agent-summary', route: '/apps/{app}/agents/new', target: 'agent-preview', placement: 'left' },
      { id: 'playground', route: '/playground?app={app}', target: 'pg-pick' },
      { id: 'playground-send', route: '/playground?app={app}', target: 'pg-composer', placement: 'top' },
      { id: 'playground-events', route: '/playground?app={app}', target: 'pg-events', placement: 'left' },
    ],
  },
  {
    id: 'security',
    steps: [
      { id: 'system', route: '/policy', target: 'sys-controls', needs: ['owner'] },
      { id: 'modes', route: '/apps/{app}/security', target: 'sec-controls' },
      { id: 'versions', route: '/apps/{app}/security', target: 'policy-history' },
      { id: 'tests', route: '/apps/{app}/security', target: 'policy-tests' },
      { id: 'events', route: '/apps/{app}/security', target: 'sec-events' },
    ],
  },
  {
    id: 'usage-traces',
    steps: [
      { id: 'all', route: '/usage', target: 'usage-all' },
      { id: 'forecast', route: '/apps/{app}/usage', target: 'usage-forecast' },
      { id: 'views', route: '/apps/{app}/usage', target: 'usage-views' },
      { id: 'traces', route: '/apps/{app}/traces', target: 'traces-list', placement: 'top' },
      { id: 'filters', route: '/apps/{app}/traces', target: 'traces-filters' },
      { id: 'versions', route: '/apps/{app}/traces', target: 'traces-views' },
    ],
  },
  // The gateway tour goes here once the gateway pages exist (create a gateway, its keys, connecting Claude Code and
  // Codex): id 'gateway', its steps pointing at `data-tour` places on those pages, its texts under `tour.gateway.*`.
]

/** The tour invited to on a first sign-in. */
export const FIRST_TOUR = 'first-steps'

export const tourById = (id: string): Tour | undefined => TOURS.find((t) => t.id === id)
