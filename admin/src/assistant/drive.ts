/**
 * The assistant drives the playground: its `playground` client tool opens the playground on an application and an
 * agent, types the message there in front of the person, and answers with what the agent replied. The playground
 * registers itself here while it shows an agent; the assistant's panel calls `drivePlayground`.
 */

/** What the agent did with one message, as the assistant reads it. */
export interface PlaygroundReply {
  application: string
  agent: string
  /** The agent's answer (its text, as it streamed). */
  answer: string
  /** The tools it used, in order. */
  tools: string[]
  /** It now waits for the person in the playground: a confirmation of a change, or a choice. */
  waiting?: 'confirm' | 'ask'
  /** The agent was still answering when the time ran out: the playground goes on, the rest is there. */
  stillAnswering?: boolean
  error?: string
}

/** The playground showing an agent, while it does. */
export interface PlaygroundDriver {
  application: string
  agent: string
  /** The agents the playground offers for this application, once listed. */
  agents: string[] | null
  send(message: string, fresh: boolean): Promise<PlaygroundReply>
}

export interface PlaygroundArgs {
  application?: unknown
  agent?: unknown
  message?: unknown
  new?: unknown
}

let current: PlaygroundDriver | null = null
const watchers = new Set<() => void>()

/** The playground registers what it shows; the returned function unregisters it. */
export function registerPlayground(d: PlaygroundDriver): () => void {
  current = d
  for (const w of watchers) w()
  return () => {
    if (current === d) current = null
  }
}

const OPEN_MS = 15_000

/** The playground's path for an application and an agent. */
export const playgroundPath = (application: string, agent: string): string =>
  `/playground?app=${encodeURIComponent(application)}&agent=${encodeURIComponent(agent)}`

function waitFor(application: string, agent: string, ms: number): Promise<PlaygroundDriver | null> {
  const ready = () => (current && current.application === application && current.agent === agent ? current : null)
  const now = ready()
  if (now) return Promise.resolve(now)
  return new Promise((resolve) => {
    const check = () => {
      const d = ready()
      if (d) finish(d)
    }
    const finish = (d: PlaygroundDriver | null) => {
      clearTimeout(timer)
      watchers.delete(check)
      resolve(d)
    }
    const timer = setTimeout(() => finish(ready()), ms)
    watchers.add(check)
  })
}

/**
 * Run the assistant's `playground` tool: open the playground on the application and agent (`go` changes the page),
 * then send the message there.
 */
export async function drivePlayground(args: PlaygroundArgs, go: (hash: string) => void, openMs = OPEN_MS): Promise<PlaygroundReply> {
  const application = typeof args.application === 'string' ? args.application.trim() : ''
  const agent = typeof args.agent === 'string' ? args.agent.trim() : ''
  const message = typeof args.message === 'string' ? args.message.trim() : ''
  if (!application || !agent || !message) throw new Error('Give application (its slug), agent and message.')
  go(`#${playgroundPath(application, agent)}`)
  const driver = await waitFor(application, agent, openMs)
  if (!driver) {
    const shown = current?.application === application ? current.agents : null
    const known = shown?.length ? ` Its agents in the playground: ${shown.join(', ')}.` : ''
    throw new Error(`The playground did not open agent ${agent} of application ${application}: unknown, or not an agent for the browser.${known}`)
  }
  return driver.send(message, args.new === true)
}
