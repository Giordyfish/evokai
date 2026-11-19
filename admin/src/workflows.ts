/** Pure helpers of the Workflows and Approvals pages: routes, tones, the flow's arrows, problem lines, YAML. */
import type { WorkflowOutlineStep, WorkflowProblem, WorkflowRunStatus, WorkflowStepStatus } from '@client/core/types'
import type { Tone } from './ui'

/** What the id after `#/apps/<slug>/workflows/` names: the list, a new workflow, a run or a workflow to edit. */
export type WorkflowView = { kind: 'list' } | { kind: 'new' } | { kind: 'run'; id: string } | { kind: 'edit'; name: string }

/** Workflow names never hold a colon, so `run:<id>` cannot clash with one. */
export function viewOf(id: string | null): WorkflowView {
  if (!id) return { kind: 'list' }
  if (id === 'new') return { kind: 'new' }
  if (id.startsWith('run:')) return { kind: 'run', id: id.slice(4) }
  return { kind: 'edit', name: id }
}

// The same address as `appHref` in state.ts (not imported: this module stays free of the browser's globals).
const href = (slug: string, id: string): string => `#/apps/${encodeURIComponent(slug)}/workflows/${encodeURIComponent(id)}`
export const runHref = (slug: string, runId: string): string => href(slug, `run:${runId}`)
export const workflowHref = (slug: string, name: string): string => href(slug, name)

export const RUN_TONE: Record<WorkflowRunStatus, Tone> = {
  running: 'vi',
  waiting: 'gold',
  done: 'ok',
  failed: 'bad',
  rejected: 'warn',
  cancelled: '',
}

export const STEP_TONE: Record<WorkflowStepStatus, Tone> = {
  pending: '',
  running: 'vi',
  waiting: 'gold',
  done: 'ok',
  failed: 'bad',
  skipped: '',
  cancelled: '',
}

/** Kinds retried after a failure: their attempts mean something (an approval runs again when it is decided). */
export const RETRIED: ReadonlySet<string> = new Set(['agent', 'tool', 'notify'])

export const RUN_STATUSES = ['all', 'active', 'waiting', 'failed', 'done', 'rejected', 'cancelled'] as const
export type RunFilter = (typeof RUN_STATUSES)[number]

/** Whether a run can be resumed, or stopped. */
export const canRetry = (s: WorkflowRunStatus, reason: string | null): boolean =>
  (s === 'failed' || s === 'cancelled') && reason !== 'invalid_input'
export const canCancel = (s: WorkflowRunStatus): boolean => s === 'running' || s === 'waiting'

/** The problems of each line of the editor (problems without a line go under line 0). */
export function problemLines(problems: WorkflowProblem[]): Map<number, WorkflowProblem[]> {
  const out = new Map<number, WorkflowProblem[]>()
  for (const p of problems) {
    const line = p.line ?? 0
    out.set(line, [...(out.get(line) ?? []), p])
  }
  return out
}

/** An arrow out of a step: to a step id, or to the end, with its label (`then`, `else`, `next`). */
export interface Arrow {
  label: 'then' | 'else' | 'next'
  to: string
}

/**
 * The arrows of a step that do not simply go to the step below: a condition's two ways, a jump with `next`. A missing
 * way of a condition goes to the step below, named here so the diagram can show it.
 */
export function arrowsOf(step: WorkflowOutlineStep, below: string | null): Arrow[] {
  const end = 'end'
  if (step.kind === 'condition') {
    return [
      { label: 'then', to: step.then ?? below ?? end },
      { label: 'else', to: step.else ?? below ?? end },
    ]
  }
  if (step.next) return [{ label: 'next', to: step.next }]
  return []
}

/** Every step id of an outline, nested ones included, in order. */
export function stepIds(steps: WorkflowOutlineStep[]): string[] {
  return steps.flatMap((s) => [s.id, ...(s.branches ?? []).flatMap((b) => stepIds(b.steps))])
}

/** A short line that says what a step does (shown under its id in the diagram). */
export function stepDetail(s: WorkflowOutlineStep): string {
  if (s.kind === 'agent') return s.agent ?? ''
  if (s.kind === 'tool') return s.tool ?? ''
  if (s.kind === 'condition') return s.if ?? ''
  if (s.kind === 'approval') return s.question ?? ''
  if (s.kind === 'wait') return s.seconds != null ? `${s.seconds}s` : (s.until ?? '')
  if (s.kind === 'notify') return s.url ?? ''
  return ''
}

const ORDER = ['name', 'title', 'description', 'timezone', 'retention_days', 'tags', 'input_schema', 'triggers', 'steps', 'output']

/** A definition with its keys in reading order (what it is, how it starts, its steps, its output). */
export function ordered<T extends Record<string, unknown>>(d: T): T {
  const keys = [...ORDER.filter((k) => k in d), ...Object.keys(d).filter((k) => !ORDER.includes(k))]
  return Object.fromEntries(keys.map((k) => [k, d[k]])) as T
}

const PLAIN = /^[A-Za-z_][A-Za-z0-9_./ -]*$/
const RESERVED = /^(true|false|null|yes|no|on|off|~)$/i

function scalar(v: unknown): string {
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  const s = String(v)
  if (s && PLAIN.test(s) && !RESERVED.test(s) && !s.endsWith(' ') && !/^\d/.test(s)) return s
  return JSON.stringify(s)
}

/** A definition as YAML (block style, strings quoted when they need to be), for the editor. */
export function toYaml(value: unknown, indent = 0): string {
  const pad = ' '.repeat(indent)
  if (Array.isArray(value)) {
    if (!value.length) return `${pad}[]\n`
    return value
      .map((item) => {
        if (item && typeof item === 'object' && !Array.isArray(item) && Object.keys(item).length) {
          const body = toYaml(item, indent + 2)
          return `${pad}- ${body.slice(indent + 2)}`
        }
        if (Array.isArray(item) && item.length) return `${pad}-\n${toYaml(item, indent + 2)}`
        return `${pad}- ${inline(item)}\n`
      })
      .join('')
  }
  if (value && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
    if (!entries.length) return `${pad}{}\n`
    return entries
      .map(([k, v]) => {
        const key = scalar(k)
        const scalars = Array.isArray(v) && v.every((x) => x === null || typeof x !== 'object')
        if (v && typeof v === 'object' && !scalars && (Array.isArray(v) ? v.length : Object.keys(v).length)) {
          return `${pad}${key}:\n${toYaml(v, indent + 2)}`
        }
        return `${pad}${key}: ${inline(v)}\n`
      })
      .join('')
  }
  return `${pad}${inline(value)}\n`
}

function inline(v: unknown): string {
  if (Array.isArray(v)) return v.length ? `[${v.map(inline).join(', ')}]` : '[]'
  if (v && typeof v === 'object') return Object.keys(v).length ? JSON.stringify(v) : '{}'
  if (typeof v === 'string' && v.includes('\n')) return JSON.stringify(v)
  return scalar(v)
}

/** The first definition the editor shows for a new workflow. */
export const TEMPLATE = `name: new-workflow
title: New workflow
input_schema:
  type: object
  properties:
    order_id: {type: string}
steps:
  - id: check
    kind: agent
    agent: my-server-agent
    input: "Check the return for order {{ input.order_id }}"
  - id: ask
    kind: approval
    question: "Go on with order {{ input.order_id }}?"
  - id: done
    kind: notify
    url: https://hooks.example.com/evokai
    body: {order_id: "{{ input.order_id }}", result: "{{ steps.check.output }}"}
`
