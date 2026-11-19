/** A word diff of two answers (longest common subsequence over words and the spaces between them). */

export interface DiffPart {
  text: string
  /** `same` in both, `del` only in the first (baseline), `add` only in the second. */
  kind: 'same' | 'add' | 'del'
}

const MAX_TOKENS = 1_500

const tokens = (s: string): string[] => s.split(/(\s+)/).filter((t) => t !== '')

/** The parts of `b` against `a`; long texts are compared as wholes (no quadratic blow-up). */
export function wordDiff(a: string, b: string): DiffPart[] {
  if (a === b) return a ? [{ text: a, kind: 'same' }] : []
  const x = tokens(a)
  const y = tokens(b)
  if (x.length * y.length > MAX_TOKENS * MAX_TOKENS)
    return [
      ...(a ? [{ text: a, kind: 'del' as const }] : []),
      ...(b ? [{ text: b, kind: 'add' as const }] : []),
    ]
  const n = x.length
  const m = y.length
  const lcs: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      lcs[i]![j] = x[i] === y[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!)
  const out: DiffPart[] = []
  const push = (text: string, kind: DiffPart['kind']) => {
    const last = out[out.length - 1]
    if (last && last.kind === kind) last.text += text
    else out.push({ text, kind })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (x[i] === y[j]) {
      push(x[i]!, 'same')
      i++
      j++
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) push(x[i++]!, 'del')
    else push(y[j++]!, 'add')
  }
  while (i < n) push(x[i++]!, 'del')
  while (j < m) push(y[j++]!, 'add')
  return out
}
