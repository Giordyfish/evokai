import { drivePlayground, playgroundPath, registerPlayground } from '../src/assistant/drive'

describe('the assistant drives the playground', () => {
  it('opens the playground on the application and agent, then sends the message there', async () => {
    const sent: Array<[string, boolean]> = []
    const went: string[] = []
    let off = () => {}
    const go = (hash: string) => {
      went.push(hash)
      // The page answers the address a moment later, as the router does.
      setTimeout(() => {
        off = registerPlayground({
          application: 'vita',
          agent: 'vita',
          agents: ['vita', 'triage'],
          send: async (message, fresh) => {
            sent.push([message, fresh])
            return { application: 'vita', agent: 'vita', answer: 'Ciao', tools: ['crm_lookup'] }
          },
        })
      }, 10)
    }
    const reply = await drivePlayground({ application: 'vita', agent: 'vita', message: ' Come va? ', new: true }, go)
    expect(went).toEqual([`#${playgroundPath('vita', 'vita')}`])
    expect(sent).toEqual([['Come va?', true]])
    expect(reply.answer).toBe('Ciao')
    // The next message goes to the playground already open.
    await drivePlayground({ application: 'vita', agent: 'vita', message: 'E poi?' }, () => {})
    expect(sent[1]).toEqual(['E poi?', false])
    off()
  })

  it('says which agents there are when the one asked for does not open', async () => {
    const off = registerPlayground({ application: 'vita', agent: 'vita', agents: ['vita', 'triage'], send: async () => ({ application: 'vita', agent: 'vita', answer: '', tools: [] }) })
    await expect(drivePlayground({ application: 'vita', agent: 'nope', message: 'hi' }, () => {}, 30)).rejects.toThrow(/vita, triage/)
    off()
  })

  it('refuses a call without application, agent or message', async () => {
    await expect(drivePlayground({ application: 'vita', agent: 'vita' }, () => {})).rejects.toThrow(/message/)
  })
})
