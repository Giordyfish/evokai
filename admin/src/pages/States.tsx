/** Development only (`npm run dev`, `#/states`): every loader, empty state, error and effect on one page. */
import { Empty, Fx, HealthOrb, Mage, Motes, PageLoader, Ring, Rune, Skeleton, sparkle, toast } from '../magic'
import { setTitle } from '../state'
import { Btn, Hero, Panel } from '../ui'
import { LoadError } from './common'

export function States() {
  setTitle('States')
  const card = (title: string, body: preact.ComponentChildren) => (
    <Panel title={title} cls="scard">
      <div class="sbody">{body}</div>
    </Panel>
  )
  return (
    <>
      <Hero title="States" sub="Every loader, empty state, error and magic moment of the console. Development builds only." />
      <h2 class="sgroup">Loaders</h2>
      <div class="grid g3">
        {card('Page loader', <PageLoader what="Inkwell Books" />)}
        {card('Button working', <Btn busy="Testing">Test</Btn>)}
        {card('Turn running', <Btn kind="pri" busy="Running" flame>Send</Btn>)}
        {card('Tool running', <span class="toolchip"><Motes />stock_report</span>)}
        {card('Panel loading', <div style="width:100%"><Skeleton rows={5} /></div>)}
        {card('Agent thinking', <div class="think"><Mage k="ember" size={64} /><span>bookshop is thinking</span></div>)}
        {card('Ring', <Ring />)}
      </div>
      <h2 class="sgroup">Empty states and errors</h2>
      <div class="grid g3">
        {card('No agents', <Empty><b>No agents yet.</b></Empty>)}
        {card('No keys', <Empty k="ember"><b>No keys yet.</b></Empty>)}
        {card('Load error', <LoadError what="agents" error={new Error('The server did not answer.')} retry={async () => {}} />)}
      </div>
      <h2 class="sgroup">Moments</h2>
      <div class="grid g3">
        {card('Health', <><HealthOrb ok /><HealthOrb ok={false} /></>)}
        {card('Save', <Btn kind="pri" check onClick={(e) => { sparkle(e.currentTarget); toast('Saved as v7', 'New conversations use it') }}>Save</Btn>)}
        {card('Key flame', <span class="kfx"><Fx k="flame" size={72} once /></span>)}
        {card('Rune', <div style="position:relative;width:100%;height:120px;overflow:hidden"><Rune size={200} cls="draw" /></div>)}
      </div>
    </>
  )
}
