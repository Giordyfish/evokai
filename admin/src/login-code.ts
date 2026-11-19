/**
 * Sign-in links: a local script asks the service for a one-time code with a key (`POST /v1/console/login-code`) and
 * opens `{base}/admin/#/login?code=...`; the console trades the code for its cookie and drops it from the URL.
 */

const split = (hash: string): [string, URLSearchParams] => {
  const [path = '', query = ''] = hash.replace(/^#/, '').split('?')
  return [path, new URLSearchParams(query)]
}

/** The one-time code a sign-in link carries in its hash, if any. */
export function codeFromHash(hash: string): string | null {
  return split(hash)[1].get('code') || null
}

/** The same hash without the code (other parameters kept). */
export function hashWithoutCode(hash: string): string {
  const [path, q] = split(hash)
  q.delete('code')
  const rest = q.toString()
  return `#${path}${rest ? `?${rest}` : ''}`
}

/** The URL to show once the code is spent: the page itself, the hash without the code. */
export function urlWithoutCode(loc: { pathname: string; search: string; hash: string }): string {
  return loc.pathname + loc.search + hashWithoutCode(loc.hash)
}

/** Trade a code for the cookie, then take it out of the address bar whatever the outcome (it works once anyway). */
export async function exchangeCode(
  code: string,
  login: (code: string) => Promise<void>,
  strip: () => void,
): Promise<{ ok: true } | { ok: false; error: unknown }> {
  try {
    await login(code)
    return { ok: true }
  } catch (error) {
    return { ok: false, error }
  } finally {
    strip()
  }
}
