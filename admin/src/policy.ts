/**
 * Policies in the console: versions, differences, tests and copies, for an application's policy and for the system
 * policy alike. Both have the same routes under different prefixes (`/v1/admin/policy`, `/v1/console/system-policy`).
 */
import { adminJson, consoleJson, EvokaiError } from './api'

export type Outcome = 'allowed' | 'flagged' | 'blocked'
export const OUTCOMES: readonly Outcome[] = ['allowed', 'flagged', 'blocked']
/** The controls the guard's input checks run, the only ones a policy test can name. */
export const TESTABLE = ['input.size', 'input.encoding', 'input.secrets', 'input.injection', 'scope', 'untrusted.injection', 'pii.model'] as const

export interface PolicyContent {
  security: Record<string, unknown> | null
  data_protection: Record<string, unknown> | null
}
export interface PolicyVersion {
  number: number
  createdAt: string
  createdBy: string
  note: string | null
  content?: PolicyContent
}
export interface PolicyChange {
  path: string[]
  change: 'added' | 'removed' | 'changed'
  before: unknown
  after: unknown
}
export interface PolicyTestCase {
  id: string
  name: string
  input: string
  outcome: Outcome | null
  controls: Record<string, Outcome> | null
  updatedAt: string
}
export interface PolicyTestResult {
  id: string
  name: string
  passed: boolean
  expected: { outcome: Outcome | null; controls: Record<string, Outcome> | null }
  got: { outcome: Outcome; controls: Record<string, Outcome> }
}
export interface Saved {
  created: boolean
  version: number
}
/** What a restore or a copy sends: a note, and `force` to save although tests fail. */
export interface SaveOptions {
  note?: string
  force?: boolean
}
export interface NewPolicyTest {
  name: string
  input: string
  outcome?: Outcome | null
  controls?: Record<string, Outcome> | null
}

/** The routes of one policy: an application's or the system's. */
export interface PolicyApi {
  versions(): Promise<{ items: PolicyVersion[] }>
  version(n: number): Promise<PolicyVersion>
  diff(from: number, to?: number): Promise<{ from: number; to: number; changes: PolicyChange[] }>
  restore(n: number, o: SaveOptions): Promise<Saved>
  copy(source: { system?: boolean; application?: string }, o: SaveOptions): Promise<Saved>
  tests(): Promise<{ items: PolicyTestCase[] }>
  addTest(t: NewPolicyTest): Promise<PolicyTestCase>
  deleteTest(id: string): Promise<{ ok: boolean }>
  run(): Promise<{ items: PolicyTestResult[]; passed: number; failed: number }>
}

function policyApi(call: <T>(method: string, path: string, body?: unknown) => Promise<T>, base: string): PolicyApi {
  return {
    versions: () => call('GET', `${base}/versions`),
    version: (n) => call('GET', `${base}/versions/${n}`),
    diff: (from, to) => call('GET', `${base}/diff?from=${from}${to ? `&to=${to}` : ''}`),
    restore: (n, o) => call('POST', `${base}/versions/${n}/restore`, o),
    copy: (source, o) => call('POST', `${base}/copy`, { ...source, ...o }),
    tests: () => call('GET', `${base}/tests`),
    addTest: (t) => call('POST', `${base}/tests`, t),
    deleteTest: (id) => call('DELETE', `${base}/tests/${encodeURIComponent(id)}`),
    run: () => call('POST', `${base}/tests/run`),
  }
}

/** An application's policy. */
export const appPolicy = (applicationId: string): PolicyApi =>
  policyApi((method, path, body) => adminJson(applicationId, method, path, body), '/v1/admin/policy')

/** The system policy (deployment owners). */
export const systemPolicy = (): PolicyApi => policyApi((method, path, body) => consoleJson(method, path, body), '/v1/console/system-policy')

/** The failing tests of a save refused because of them (409 `policy_tests_failed`), else null. */
export function testFailures(e: unknown): PolicyTestResult[] | null {
  if (!(e instanceof EvokaiError) || e.code !== 'policy_tests_failed') return null
  const failures = (e.body as { failures?: PolicyTestResult[] } | undefined)?.failures
  return failures ?? []
}

/** A setting's path as a person reads it: `security › modes › input.injection`. */
export const pathText = (path: string[]): string => path.join(' › ')

/** A setting's value, short: lists and objects as JSON, nothing as a dash. */
export function valueText(v: unknown): string {
  if (v === null || v === undefined) return '-'
  return typeof v === 'string' ? v : JSON.stringify(v)
}

/** What a test expects, as `outcome, control: outcome`. */
export function expectText(t: { outcome: Outcome | null; controls: Record<string, Outcome> | null }): string {
  const parts: string[] = t.outcome ? [t.outcome] : []
  for (const [c, o] of Object.entries(t.controls ?? {})) parts.push(`${c}: ${o}`)
  return parts.join(', ')
}

/** A new test from the form: per-control expectations left at `any` are dropped. */
export function newTest(name: string, input: string, outcome: Outcome | 'any', controls: Record<string, Outcome | 'any'>): NewPolicyTest {
  const picked = Object.fromEntries(Object.entries(controls).filter(([, o]) => o !== 'any')) as Record<string, Outcome>
  return {
    name: name.trim(),
    input,
    outcome: outcome === 'any' ? null : outcome,
    controls: Object.keys(picked).length ? picked : null,
  }
}
