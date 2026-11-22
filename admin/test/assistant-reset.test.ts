import { setLocale } from '../src/i18n'
import { deleted, kindText, type ResetPreview, resetExport, slugTyped } from '../src/assistant/reset'

afterEach(() => setLocale('en'))

const preview: ResetPreview = {
  slug: 'evokai-assistant',
  items: [
    { kind: 'agent', name: 'assistant', action: 'restore' },
    { kind: 'prompt', name: 'extra', action: 'delete', item: { text: 'Say hello.' } },
    { kind: 'workflow', name: 'flow', action: 'delete', item: { steps: [] } },
    { kind: 'settings', name: 'grants', action: 'restore' },
  ],
}

describe('the reset preview', () => {
  it('downloads exactly the deleted items with their definitions', () => {
    const f = resetExport(preview, new Date('2026-11-22T10:00:00Z'))
    expect(f.filename).toBe('evokai-assistant-reset-2026-11-22.json')
    const data = JSON.parse(f.text)
    expect(data.application).toBe('evokai-assistant')
    expect(data.deleted).toEqual(deleted(preview))
    expect(data.deleted.map((i: { name: string }) => i.name)).toEqual(['extra', 'flow'])
    expect(data.deleted[0].item.text).toBe('Say hello.')
  })
  it('needs the typed slug', () => {
    expect(slugTyped('', 'evokai-assistant')).toBe(false)
    expect(slugTyped('evokai', 'evokai-assistant')).toBe(false)
    expect(slugTyped(' evokai-assistant ', 'evokai-assistant')).toBe(true)
  })
  it('names the kinds in both languages', () => {
    expect(kindText('knowledge')).toBe('Knowledge base')
    expect(kindText('something-new')).toBe('something-new')
    setLocale('it')
    expect(kindText('knowledge')).toBe('Base di conoscenza')
  })
})
