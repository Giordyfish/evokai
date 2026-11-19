// Screenshots of the panel's views and of the orb in flight, against the offline page (demo/fake.html: a scripted
// Evokai and a fake recogniser, no backend). It starts its own dev server.
//
//   node e2e/voice-panel.mjs [screenshots folder] [--skin skin.mjs]
//
// `--skin` is a module exporting any of `skin`, `icons`, `strings`, `css` (a stylesheet for the page, e.g. the
// application's --evokai-* tokens), `locale`, `title`, `line` (what the agent says) and `pageHtml` (the page behind), to
// see the panel in an application's look.
// Each run takes the light and dark schemes at desktop width, and the main views at phone width.
import { mkdirSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { chromium } from 'playwright'
import { createServer } from 'vite'

const args = process.argv.slice(2)
const skinAt = args.indexOf('--skin')
const skinFile = skinAt >= 0 ? args.splice(skinAt, 2)[1] : null
const out = args[0] ?? 'test-results/voice-panel'
mkdirSync(out, { recursive: true })
const look = skinFile ? await import(pathToFileURL(skinFile).href) : {}

const server = await createServer({
  configFile: fileURLToPath(new URL('../demo/vite.config.ts', import.meta.url)),
  server: { port: 5175, strictPort: false },
  logLevel: 'error',
})
await server.listen()
const base = server.resolvedUrls.local[0]
const browser = await chromium.launch()
const wait = (ms) => new Promise((r) => setTimeout(r, ms))

async function page(scheme, viewport) {
  const p = await browser.newPage({ viewport, colorScheme: scheme })
  const errors = []
  p.on('pageerror', (e) => errors.push(String(e)))
  const url = `${base}fake.html?scheme=${scheme}${look.locale ? `&locale=${look.locale}` : ''}`
  // The dev server may reload the page once while it optimises the dependencies.
  for (let i = 0; i < 3; i++) {
    await p.goto(url)
    const ok = await p.waitForFunction(() => window.evokaiFake, null, { timeout: 15_000 }).then(() => true, () => false)
    await wait(300)
    if (ok && (await p.evaluate(() => !!window.evokaiFake).catch(() => false))) break
  }
  await p.evaluate(
    ({ skin, icons, strings, css, pageHtml, title }) => {
      const chat = document.getElementById('chat')
      if (title) chat.setAttribute('title', title)
      if (pageHtml) {
        document.querySelector('header')?.remove()
        document.querySelector('main').outerHTML = pageHtml
      }
      if (css) document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`)
      if (skin) chat.skin = skin
      if (icons) chat.icons = icons
      if (strings) chat.strings = strings
      if (document.documentElement && skin) document.documentElement.classList.toggle('dark', chat.getAttribute('scheme') === 'dark')
    },
    { skin: look.skin ?? null, icons: look.icons ?? null, strings: look.strings ?? null, css: look.css ?? null, pageHtml: look.pageHtml ?? null, title: look.title ?? null },
  )
  await wait(400)
  return { p, errors }
}

const chat = (p) => p.locator('evokai-chat')
const voice = (p, o) => p.evaluate((o) => window.evokaiFake.voice(o), o)
const orbs = (p) => p.evaluate(() => document.getElementById('chat').shadowRoot.querySelectorAll('canvas.orbgl').length)
const LINE = look.line ?? 'Damaged in transit leads with twelve returns, the most of the three reasons. I highlighted it in the chart for you.'

async function desktop(scheme) {
  const { p, errors } = await page(scheme, { width: 1280, height: 800 })
  const shot = (name) => p.screenshot({ path: `${out}/${scheme}-${name}.png` })
  await shot('01-closed')
  await chat(p).locator('button.launcher').click()
  await wait(320)
  await shot('02-open-orb-flying')
  await wait(470)
  await shot('03-open-box-growing')
  await wait(700)
  await shot('04-chat')
  const box = chat(p).locator('textarea')
  await box.fill('Which reason has the most returns?')
  await box.press('Enter')
  await chat(p).locator('figure').first().waitFor()
  await wait(400)
  await shot('05-chat-answer')
  await chat(p).locator('[part~="voice-button"]').click()
  await wait(330)
  await shot('06-voice-orb-flying')
  await wait(900)
  await voice(p, { phase: 'listening', heard: '' })
  await wait(400)
  await shot('07-voice-listening')
  await voice(p, { phase: 'listening', heard: 'Which reason has the most', user: 0.7 })
  await wait(500)
  await shot('08-voice-user-speaks')
  await voice(p, { phase: 'thinking', heard: '', user: 0 })
  await wait(500)
  await shot('09-voice-thinking')
  await voice(p, { phase: 'speaking', line: LINE, upto: 46, agent: 0.5 })
  await wait(600)
  await shot('10-voice-speaking')
  await voice(p, { phase: 'interrupted', line: '', heard: '' })
  await wait(400)
  await shot('11-voice-interrupted')
  await voice(p, { phase: 'speaking', line: LINE, upto: 70, agent: 0.5 })
  await wait(200)
  await chat(p).locator('[part~="minimize-button"]').click()
  await wait(140)
  await shot('12-minimize-box-closing')
  await wait(420)
  await shot('13-minimize-orb-flying')
  await wait(1100)
  await shot('14-minimized-speaking')
  if ((await orbs(p)) !== 1) errors.push('more than one orb')
  await chat(p).locator('button.launcher').hover()
  await wait(400)
  await shot('15-minimized-hover')
  await p.mouse.move(600, 300)
  await voice(p, { phase: 'listening', line: '', heard: '' })
  await wait(400)
  await shot('16-minimized-listening')
  await voice(p, { phase: 'thinking' })
  await wait(400)
  await shot('17-minimized-thinking')
  await voice(p, { phase: 'listening', heard: 'Show me the chart', user: 0.6 })
  await wait(400)
  await shot('18-minimized-user-speaks')
  await chat(p).locator('button.launcher').click()
  await wait(380)
  await shot('19-restore-orb-flying')
  await wait(1100)
  await voice(p, { phase: 'listening', heard: '', user: 0 })
  await wait(300)
  await shot('20-restored-voice')
  await chat(p).locator('[part~="close-button"]').click()
  await wait(130)
  await shot('21-close-box-closing')
  await wait(450)
  await shot('22-close-orb-flying')
  await wait(1000)
  await shot('23-closed-again')
  const view = await chat(p).getAttribute('data-view')
  if (view !== 'closed') errors.push(`view after close: ${view}`)
  await p.close()
  return errors
}

async function phone(scheme) {
  const { p, errors } = await page(scheme, { width: 390, height: 844 })
  const shot = (name) => p.screenshot({ path: `${out}/phone-${scheme}-${name}.png` })
  await shot('01-closed')
  await chat(p).locator('button.launcher').click()
  await wait(1500)
  await shot('02-chat')
  await chat(p).locator('[part~="voice-button"]').click()
  await wait(1300)
  await voice(p, { phase: 'speaking', line: LINE, upto: 46, agent: 0.5 })
  await wait(500)
  await shot('03-voice')
  await chat(p).locator('[part~="minimize-button"]').click()
  await wait(1600)
  await shot('04-minimized')
  await chat(p).locator('button.launcher').click()
  await wait(1500)
  await shot('05-restored')
  await p.close()
  return errors
}

const errors = [...(await desktop('light')), ...(await desktop('dark')), ...(await phone('light'))]
await browser.close()
await server.close()
if (errors.length) {
  console.error(`voice panel: ${errors.join('; ')}`)
  process.exit(1)
}
console.log(`voice panel: ok (${out})`)
