/** Where the API is mounted: the console lives at `{base}/admin/` (in development, `VITE_EVOKAI_BASE` or `/agent`). */
export function apiBase(path: string): string {
  const i = path.indexOf('/admin')
  if (i >= 0) return path.slice(0, i)
  return (import.meta.env?.VITE_EVOKAI_BASE as string | undefined) ?? '/agent'
}
