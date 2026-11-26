/**
 * Gateways for coding tools in the console: the admin API (`/v1/admin/gateways`), the ready snippets a person pastes in
 * their tool, and the employee notice. A gateway's policy is edited with the application policy routes, the gateway
 * named in `X-Evokai-Application` (see `src/pages/Gateways.tsx`).
 */
import type { Gateway, GatewayPerson, GatewayProvider, GatewayUsage } from '@client/core/types'
import { baseUrl, consoleJson } from './api'

export const PROVIDERS: readonly GatewayProvider[] = ['anthropic', 'openai']
/** The tools each provider's door serves. */
export const TOOL: Record<GatewayProvider, string> = {
  anthropic: 'Claude Code',
  openai: 'Codex',
}
/** The guard's controls that act on a gateway; the policy's other controls do nothing there. */
export const GATEWAY_CONTROLS = ['input.size', 'input.secrets', 'pii.model', 'scope', 'abuse.rate'] as const
/** The tabs of a gateway's page, in their order. */
export const GW_TABS = ['people', 'policy', 'usage', 'events', 'connect', 'notice'] as const
export type GwTab = (typeof GW_TABS)[number]

const BASE = '/v1/admin/gateways'
const one = (id: string): string => `${BASE}/${encodeURIComponent(id)}`

export const gateways = {
  list: () => consoleJson<{ items: Gateway[]; canCreate: boolean }>('GET', BASE),
  create: (body: { slug: string; name: string; providers: GatewayProvider[]; allowed_models?: string[] }) => consoleJson<Gateway>('POST', BASE, body),
  get: (id: string) => consoleJson<Gateway>('GET', one(id)),
  patch: (id: string, body: Record<string, unknown>) => consoleJson<Gateway>('PATCH', one(id), body),
  people: (id: string) => consoleJson<{ items: GatewayPerson[] }>('GET', `${one(id)}/people`),
  addPerson: (id: string, label: string) => consoleJson<{ id: string; label: string; key: string }>('POST', `${one(id)}/people`, { label }),
  revoke: (id: string, keyId: string) => consoleJson<{ ok: boolean }>('DELETE', `${one(id)}/people/${encodeURIComponent(keyId)}`),
  usage: (id: string, days = 30) => consoleJson<GatewayUsage>('GET', `${one(id)}/usage?days=${days}`),
}

export const gwHref = (slug: string, tab: GwTab = 'people'): string => `#/gateways/${encodeURIComponent(slug)}${tab === 'people' ? '' : `/${tab}`}`

/** This deployment's door for a provider (what the tool's base URL is). */
export const doorUrl = (provider: GatewayProvider): string => `${baseUrl}/gw/${provider}`

/** A placeholder in place of a person's key: their key is shown once, to them, when it is made. */
export const KEY_PLACEHOLDER = 'evk_gw_...'

/**
 * Claude Code: the organisation's managed settings (claude.ai admin settings or the managed settings file) point every
 * seat at the gateway; each person's own `~/.claude/settings.json` adds their key.
 */
export function claudeManaged(): string {
  return JSON.stringify({ env: { ANTHROPIC_BASE_URL: doorUrl('anthropic') } }, null, 2)
}

export function claudeUser(key = KEY_PLACEHOLDER): string {
  return JSON.stringify(
    {
      env: {
        ANTHROPIC_BASE_URL: doorUrl('anthropic'),
        ANTHROPIC_CUSTOM_HEADERS: `x-evokai-key: ${key}`,
      },
    },
    null,
    2,
  )
}

/** The same from a shell, for a first try. */
export function claudeShell(key = KEY_PLACEHOLDER): string {
  return [`export ANTHROPIC_BASE_URL=${doorUrl('anthropic')}`, `export ANTHROPIC_CUSTOM_HEADERS="x-evokai-key: ${key}"`, 'claude'].join('\n')
}

/** Codex: a model provider in `~/.codex/config.toml` that keeps the person's ChatGPT login and sends their key. */
export function codexConfig(): string {
  return [
    'model_provider = "evokai"',
    '',
    '[model_providers.evokai]',
    'name = "Evokai"',
    `base_url = "${doorUrl('openai')}/v1"`,
    'wire_api = "responses"',
    'requires_openai_auth = true',
    'env_http_headers = { "x-evokai-key" = "EVOKAI_GATEWAY_KEY" }',
  ].join('\n')
}

/** The key Codex reads from the environment (its config names the variable, never the key). */
export function codexShell(key = KEY_PLACEHOLDER): string {
  return [`export EVOKAI_GATEWAY_KEY=${key}`, 'codex'].join('\n')
}
