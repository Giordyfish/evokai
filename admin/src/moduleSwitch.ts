/** The modules of the deployment in the console (`evokai.core.modules`): a module that is off hides its pages. */
import type { ConsoleMe, EvokaiModule } from '@client/core/types'
import type { Route } from './state'

/** Whether a module is on (both are when the service does not say: an older service). */
export const moduleOn = (me: Pick<ConsoleMe, 'modules'>, name: EvokaiModule): boolean =>
  (me.modules ?? ['applications', 'gateway']).includes(name)

const APPLICATION_VIEWS: ReadonlyArray<Route['view']> = ['app', 'apps', 'setup', 'playground', 'usage', 'assistant']

/** The module a view belongs to, or null for the core (health, access, system policy, modules, preferences). */
export function moduleOf(route: Pick<Route, 'view'>): EvokaiModule | null {
  if (route.view === 'gateways' || route.view === 'proxy') return 'gateway'
  return APPLICATION_VIEWS.includes(route.view) ? 'applications' : null
}

/** Whether a login reads the gateway proxy's status page: the operator key, or an operator or owner of every gateway. */
export const proxyReader = (me: Pick<ConsoleMe, 'deploymentOwner' | 'roles' | 'kind'>): boolean =>
  !!me.deploymentOwner || (me.kind === 'sso' && (me.roles?.['*'] === 'operator' || me.roles?.['*'] === 'owner'))
