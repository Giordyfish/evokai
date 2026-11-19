import { defineElements, type Theme } from '../src/index'

defineElements()

const chat = document.getElementById('chat') as HTMLElementTagNameMap['evokai-chat']
const stage = document.getElementById('stage') as HTMLElementTagNameMap['evokai-voice']
const params = new URLSearchParams(location.search)
const base = (import.meta.env.VITE_EVOKAI_URL as string | undefined) ?? params.get('evokai')
if (base) chat.setAttribute('base-url', base)

const custom: Theme = {
  light: { brand: '#0b3d91', onBrand: '#ffffff', accent: '#ff6b35', series: ['#0b3d91', '#8fb3ff', '#ff6b35', '#2a9d8f', '#e9c46a'] },
  dark: { brand: '#8fb3ff', onBrand: '#06173a', accent: '#ff9f6b' },
  radius: '14px',
  font: 'Georgia, serif',
  orb: { ink: '#06173a', main: '#0b3d91', light: '#8fb3ff', glow: '#fff4ec' },
}

chat.toolLabels = { orders_search: 'Searching the orders', order_get: 'Reading the order' }

const theme = document.getElementById('theme') as HTMLSelectElement
const locale = document.getElementById('locale') as HTMLSelectElement
const scheme = document.getElementById('scheme') as HTMLSelectElement
const apply = () => {
  for (const el of [chat, stage]) {
    el.theme = theme.value === 'custom' ? custom : theme.value
    el.setAttribute('locale', locale.value)
    if (scheme.value) el.setAttribute('scheme', scheme.value)
    else el.removeAttribute('scheme')
  }
}
theme.addEventListener('change', apply)
locale.addEventListener('change', apply)
scheme.addEventListener('change', apply)
apply()

document.getElementById('voice')?.addEventListener('click', () => void stage.start())
chat.addEventListener('evokai-navigate', (e) => {
  // A single-page application routes here; the demo just shows where the agent wanted to go.
  e.preventDefault()
  history.pushState(null, '', (e as CustomEvent<{ path: string }>).detail.path)
})
if (params.has('open')) chat.open()
