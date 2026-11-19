/** The data protection profile as the console's form edits it. */
import type { DataProtection } from '@client/core/types'

export const GEOS = ['eu', 'us', 'apac', 'global'] as const
export const RETENTION = ['any', 'no_training', 'zero_retention'] as const

const list = (s: string): string[] =>
  s
    .split(/[\s,]+/)
    .map((x) => x.trim())
    .filter(Boolean)

/** The profile as the form edits it (null when nothing is set). */
export function profileFrom(f: {
  geos: string[]
  countries: string
  eu: boolean
  jurisdictions: string
  retention: (typeof RETENTION)[number]
  voices: boolean
  recognition: boolean
  vendors: string
}): DataProtection | null {
  const out: DataProtection = {}
  const countries = list(f.countries).map((c) => c.toUpperCase())
  if (f.geos.length || countries.length) {
    out.residency = {}
    if (f.geos.length) out.residency.geographies = f.geos
    if (countries.length) out.residency.countries = countries
  }
  const j = [...(f.eu ? ['eu'] : []), ...list(f.jurisdictions).map((c) => c.toUpperCase())]
  if (j.length) out.jurisdiction = j
  if (f.retention !== 'any') out.retention = f.retention
  const vendors = list(f.vendors)
  if (f.voices || f.recognition || vendors.length) {
    out.browser = { online_voices: f.voices, online_recognition: f.recognition }
    if (vendors.length) out.browser.vendors = vendors
  }
  return Object.keys(out).length ? out : null
}

