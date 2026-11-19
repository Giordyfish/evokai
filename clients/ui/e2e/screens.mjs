// Screenshots of the components against a running demo (`npm run demo`): a chart, a question card and voice mode in
// the panel (with a fake on-device recogniser, as headless browsers have none). For every view of the panel and the
// orb in flight, without a backend, see voice-panel.mjs.
//
//   node e2e/screens.mjs [demo URL] [screenshots folder]
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright'

const url = process.argv[2] ?? 'http://localhost:5174/'
const out = process.argv[3] ?? 'test-results'
mkdirSync(out, { recursive: true })

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 860 } })
await page.addInitScript(() => {
  // A recogniser that is "installed" and never hears anything, and no voices: enough to open the stage.
  class Fake {
    start() {}
    stop() {}
    abort() {}
  }
  Fake.available = async () => 'available'
  Fake.install = async () => true
  window.SpeechRecognition = Fake
  window.webkitSpeechRecognition = Fake
})
await page.goto(url)
const chat = page.locator('evokai-chat')
await page.keyboard.press('Control+j')
const ask = async (text) => {
  const box = chat.locator('textarea')
  await box.fill(text)
  await box.press('Enter')
  await chat.locator('button.send[aria-label="Send"]').waitFor({ timeout: 120_000 })
}
await ask('Draw a column chart of the orders per status on this page, with the ui_chart tool.')
await page.screenshot({ path: `${out}/panel-chart.png` })
await ask('Ask me, with the ui_ask tool, which order to open first (the three order numbers as options).')
await page.screenshot({ path: `${out}/panel-ask.png` })
await chat.locator('button[aria-label="Voice mode"]').click()
await chat.locator('.panel[data-layer=voice]').waitFor()
await page.waitForTimeout(1400)
await page.screenshot({ path: `${out}/voice-panel.png` })
const focused = await page.evaluate(() => document.querySelector('evokai-chat').shadowRoot.activeElement?.getAttribute('aria-label'))
console.log(`voice focus on: ${focused}`)
await page.keyboard.press('Escape')
await chat.locator('.panel[data-layer=chat]').waitFor()
await browser.close()
console.log(`screens: ok (${out})`)
