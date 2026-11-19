// Playwright smoke test of the demo against a running Evokai (`npm run demo` first):
//
//   node e2e/smoke.mjs [demo URL] [screenshots folder]
//
// Opens the panel, asks a question, waits for the answer, checks the keyboard and the accessible names, and takes
// screenshots in the default theme (light and dark) and with a custom theme object.
import { mkdirSync } from 'node:fs'
import { chromium } from 'playwright'

const url = process.argv[2] ?? 'http://localhost:5174/'
const out = process.argv[3] ?? 'test-results'
mkdirSync(out, { recursive: true })
const fail = (m) => {
  console.error(`smoke: ${m}`)
  process.exit(1)
}

const browser = await chromium.launch()
try {
  for (const [name, scheme, theme] of [
    ['light', 'light', 'evokai'],
    ['dark', 'dark', 'evokai'],
    ['custom', 'light', 'custom'],
  ]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, colorScheme: scheme })
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    // Headless Chromium crashes on SpeechRecognition.available(): report on-device recognition as unavailable.
    await page.addInitScript(() => {
      for (const C of [window.SpeechRecognition, window.webkitSpeechRecognition]) if (C) C.available = async () => 'unavailable'
    })
    await page.goto(url)
    await page.selectOption('#theme', theme)
    const launcher = page.locator('evokai-chat').locator('button.launcher')
    if (!(await launcher.getAttribute('aria-label'))?.startsWith('Open')) fail('the launcher has no accessible name')
    await page.keyboard.press('Control+j')
    const panel = page.locator('evokai-chat').locator('section.panel')
    await panel.waitFor()
    if ((await panel.getAttribute('role')) !== 'dialog') fail('the panel is not a dialog')
    const box = page.locator('evokai-chat').locator('textarea')
    await box.fill('In one sentence: what is on this page?')
    await box.press('Enter')
    await page.locator('evokai-chat').locator('.msg.assistant .md').first().waitFor({ timeout: 90_000 })
    await page.locator('evokai-chat').locator('button.send[aria-label="Send"]').waitFor({ timeout: 90_000 })
    await page.screenshot({ path: `${out}/panel-${name}.png` })
    await page.keyboard.press('Escape')
    // The box closes first, then the orb flies back to the launcher.
    if (!(await panel.waitFor({ state: 'detached', timeout: 3000 }).then(() => true, () => false))) fail('Escape did not close the panel')
    if (errors.length) fail(`page errors: ${errors.join('; ')}`)
    await page.close()
  }
  console.log(`smoke: ok (screenshots in ${out})`)
} finally {
  await browser.close()
}
