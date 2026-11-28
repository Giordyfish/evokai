/**
 * Gateways for coding tools in the console: the admin API (`/v1/admin/gateways`), the ready snippets a person pastes in
 * their tool, and the employee notice. A gateway's policy is edited with the application policy routes, the gateway
 * named in `X-Evokai-Application` (see `src/pages/Gateways.tsx`).
 */
import type { Gateway, GatewayPerson, GatewayProvider, GatewayUsage, ProxySettings, ProxySetup, ProxyStatus } from '@client/core/types'
import { baseUrl, consoleJson } from './api'

/** The providers a gateway may enable when the service does not list them (the gateway list returns its own). */
export const PROVIDERS: readonly GatewayProvider[] = ['anthropic', 'openai', 'github']
/** The tools each provider serves (GitHub Copilot through the gateway proxy only: it has no door). */
export const TOOL: Record<GatewayProvider, string> = {
  anthropic: 'Claude Code',
  openai: 'Codex',
  github: 'GitHub Copilot',
}
/** A provider's tool name, or the provider itself for one this console does not know. */
export const toolOf = (p: string): string => TOOL[p as GatewayProvider] ?? p
/** The apps the gateway proxy serves for each catalogue provider. */
export const PROXY_APPS: Record<string, string> = {
  anthropic: 'Claude Desktop, Claude Code',
  openai: 'ChatGPT, Codex',
  github: 'GitHub Copilot',
}
/** The guard's controls that act on a gateway; the policy's other controls do nothing there. */
export const GATEWAY_CONTROLS = ['input.size', 'input.secrets', 'pii.model', 'scope', 'abuse.rate'] as const
/** The tabs of a gateway's page, in their order. */
export const GW_TABS = ['people', 'policy', 'usage', 'events', 'connect', 'notice'] as const
export type GwTab = (typeof GW_TABS)[number]

const BASE = '/v1/admin/gateways'
const one = (id: string): string => `${BASE}/${encodeURIComponent(id)}`

export const gateways = {
  list: () => consoleJson<{ items: Gateway[]; canCreate: boolean; providers?: GatewayProvider[] }>('GET', BASE),
  create: (body: { slug: string; name: string; providers: GatewayProvider[]; allowed_models?: string[] }) => consoleJson<Gateway>('POST', BASE, body),
  get: (id: string) => consoleJson<Gateway>('GET', one(id)),
  patch: (id: string, body: Record<string, unknown>) => consoleJson<Gateway>('PATCH', one(id), body),
  people: (id: string) => consoleJson<{ items: GatewayPerson[] }>('GET', `${one(id)}/people`),
  addPerson: (id: string, label: string, email?: string) =>
    consoleJson<{ id: string; label: string; email: string | null; key: string }>('POST', `${one(id)}/people`, email ? { label, email } : { label }),
  changePerson: (id: string, keyId: string, body: { label?: string; email?: string }) =>
    consoleJson<{ id: string; label: string; email: string | null }>('PATCH', `${one(id)}/people/${encodeURIComponent(keyId)}`, body),
  revoke: (id: string, keyId: string) => consoleJson<{ ok: boolean }>('DELETE', `${one(id)}/people/${encodeURIComponent(keyId)}`),
  usage: (id: string, days = 30) => consoleJson<GatewayUsage>('GET', `${one(id)}/usage?days=${days}`),
}

/** The gateway proxy (`/v1/admin/proxy`): what PCs need, its status page and its console settings. */
export const proxy = {
  setup: () => consoleJson<ProxySetup>('GET', '/v1/admin/proxy/setup'),
  status: () => consoleJson<ProxyStatus>('GET', '/v1/admin/proxy'),
  save: (body: ProxySettings) => consoleJson<ProxyStatus['settings']>('PUT', '/v1/admin/proxy/settings', body),
  reset: () => consoleJson<ProxyStatus['settings']>('DELETE', '/v1/admin/proxy/settings'),
}

/** A plain e-mail check, as the service's (`name@domain.tld`). */
export const isEmail = (s: string): boolean => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s.trim())

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

// ── the company network: PCs that go through the gateway proxy ───────────────────────────────────────────────────

/** Placeholders shown until the deployment sets the proxy's address. */
export const PROXY_PLACEHOLDER = 'proxy.example.com:3128'
export const PAC_PLACEHOLDER = 'https://evokai.example.com/proxy.pac'

/** The values the snippets need, with placeholders for what the deployment has not set yet. */
export interface NetValues {
  address: string
  pac: string
  /** The PAC's fallback: `direct` lets a PC go around an unreachable proxy. */
  direct: boolean
}

export function netValues(s: ProxySetup | null): NetValues {
  return { address: s?.publicAddress || PROXY_PLACEHOLDER, pac: s?.pacUrl || PAC_PLACEHOLDER, direct: s?.pacFallback === 'direct' }
}

/** The proxy as a URL, for the variable command-line tools read (`HTTPS_PROXY`). */
export const proxyUrl = (v: NetValues): string => `http://${v.address}`

/** Where the snippets save the root certificate on a PC. */
export const ROOT_FILE = {
  windows: 'C:\\ProgramData\\Evokai\\evokai-proxy-root.crt',
  mac: '/Library/Application Support/Evokai/evokai-proxy-root.crt',
  linux: '/usr/local/share/ca-certificates/evokai-proxy-root.crt',
} as const

const xml = (s: string): string => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * A macOS configuration profile: the root certificate (trusted) and the proxy settings (automatic, from the PAC). `der`
 * is the root in DER, base64; without it the profile marks where to paste it.
 */
export function macProfile(v: NetValues, der: string | null, ids: readonly [string, string, string]): string {
  const data = der ? der.replace(/(.{64})/g, '$1\n').trim() : 'PASTE_THE_ROOT_CERTIFICATE_DER_BASE64_HERE'
  return [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
    '<plist version="1.0">',
    '<dict>',
    '  <key>PayloadType</key><string>Configuration</string>',
    '  <key>PayloadVersion</key><integer>1</integer>',
    '  <key>PayloadIdentifier</key><string>evokai.proxy</string>',
    `  <key>PayloadUUID</key><string>${ids[0]}</string>`,
    '  <key>PayloadDisplayName</key><string>Evokai gateway proxy</string>',
    '  <key>PayloadScope</key><string>System</string>',
    '  <key>PayloadContent</key>',
    '  <array>',
    '    <dict>',
    '      <key>PayloadType</key><string>com.apple.security.root</string>',
    '      <key>PayloadVersion</key><integer>1</integer>',
    '      <key>PayloadIdentifier</key><string>evokai.proxy.root</string>',
    `      <key>PayloadUUID</key><string>${ids[1]}</string>`,
    '      <key>PayloadDisplayName</key><string>Evokai proxy root</string>',
    '      <key>PayloadCertificateFileName</key><string>evokai-proxy-root.cer</string>',
    '      <key>PayloadContent</key>',
    `      <data>\n${data}\n      </data>`,
    '    </dict>',
    '    <dict>',
    '      <key>PayloadType</key><string>com.apple.proxy.http.global</string>',
    '      <key>PayloadVersion</key><integer>1</integer>',
    '      <key>PayloadIdentifier</key><string>evokai.proxy.pac</string>',
    `      <key>PayloadUUID</key><string>${ids[2]}</string>`,
    '      <key>PayloadDisplayName</key><string>Evokai proxy PAC</string>',
    '      <key>ProxyType</key><string>Auto</string>',
    `      <key>ProxyPACURL</key><string>${xml(v.pac)}</string>`,
    `      <key>ProxyPACFallbackAllowed</key><${v.direct ? 'true' : 'false'}/>`,
    '    </dict>',
    '  </array>',
    '</dict>',
    '</plist>',
  ].join('\n')
}

/** Windows without device management (a test PC): the root in the machine store, the PAC for the signed-in user. */
export function windowsShell(v: NetValues): string {
  return [
    `certutil -addstore -f Root ${ROOT_FILE.windows}`,
    `reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings" /v AutoConfigURL /t REG_SZ /d "${v.pac}" /f`,
  ].join('\n')
}

/** Linux: the root in the system store (Debian and Ubuntu, or p11-kit on Fedora and Arch), the PAC in GNOME. */
export function linuxShell(v: NetValues): string {
  return [
    `sudo cp evokai-proxy-root.crt ${ROOT_FILE.linux} && sudo update-ca-certificates`,
    'sudo trust anchor --store evokai-proxy-root.crt',
    "gsettings set org.gnome.system.proxy mode 'auto'",
    `gsettings set org.gnome.system.proxy autoconfig-url '${v.pac}'`,
  ].join('\n')
}

/** Claude Desktop's managed key, per system: it reads the PAC itself, whatever the system proxy is. */
export function desktopMac(v: NetValues): string {
  return ['<!-- com.anthropic.claudefordesktop -->', '<key>egressProxyPacUrl</key>', `<string>${xml(v.pac)}</string>`].join('\n')
}

export function desktopWindows(v: NetValues): string {
  return ['Windows Registry Editor Version 5.00', '', '[HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Claude]', `"egressProxyPacUrl"="${v.pac}"`].join('\n')
}

export function desktopLinux(v: NetValues): string {
  return JSON.stringify({ egressProxyPacUrl: v.pac }, null, 2)
}

/** Claude Code's managed settings: its requests through the proxy, the root added to Node's. */
export function claudeCodeNetwork(v: NetValues, root: string = ROOT_FILE.mac): string {
  return JSON.stringify({ env: { HTTPS_PROXY: proxyUrl(v), NODE_EXTRA_CA_CERTS: root } }, null, 2)
}

/**
 * Codex: the proxy and the root certificate as system variables (Windows, then macOS; Linux as macOS with its path).
 * Codex reads the extra root from `CODEX_CA_CERTIFICATE`, else `SSL_CERT_FILE`.
 */
export function codexNetwork(v: NetValues): string {
  return [
    `setx /M HTTPS_PROXY ${proxyUrl(v)}`,
    `setx /M CODEX_CA_CERTIFICATE ${ROOT_FILE.windows}`,
    `export HTTPS_PROXY=${proxyUrl(v)}`,
    `export CODEX_CA_CERTIFICATE="${ROOT_FILE.mac}"`,
  ].join('\n')
}

/** VS Code (GitHub Copilot): the system's proxy and certificates. These are its defaults, stated. */
export function vscodeNetwork(): string {
  return JSON.stringify({ 'http.proxySupport': 'override', 'http.systemCertificates': true }, null, 2)
}

/** Firefox's enterprise policy (`policies.json`): trust the roots of the system store, use the system proxy. */
export function firefoxPolicy(): string {
  return JSON.stringify({ policies: { Certificates: { ImportEnterpriseRoots: true }, Proxy: { Mode: 'system' } } }, null, 2)
}

/** The root certificate in DER, base64 (for the macOS profile), or null when it cannot be read. */
export async function rootDer(url: string | null): Promise<string | null> {
  if (!url) return null
  try {
    const r = await fetch(url)
    if (!r.ok) return null
    const bytes = new Uint8Array(await r.arrayBuffer())
    let bin = ''
    for (const b of bytes) bin += String.fromCharCode(b)
    return btoa(bin)
  } catch {
    return null
  }
}
