/// <reference types="node" />
import { existsSync, readFileSync } from 'node:fs'
// Italian cases ported from the server's tests/test_speech_and_visuals.py; English cases follow the en pack.
import { SentenceSplitter, isEcho, speakable, spokenDecision, spokenOption } from '../src/browser/speech'
import { localePack, locales } from '../src/locales'

describe('what gets spoken (it)', () => {
  test('sentences come out as soon as they end, numbers and abbreviations do not split them', () => {
    const s = new SentenceSplitter(0, 'it')
    expect(s.push('Rimborsi per **4.572 €**, −1,2% sulla settimana scor')).toEqual([])
    expect(s.push('sa. Vedi es. l’ordine 1042')).toEqual(['Rimborsi per 4.572 €, −1,2% sulla settimana scorsa.'])
    expect(s.flush()).toEqual(['Vedi es. l’ordine 1042'])
  })

  test('tables, links, headings and list marks are not read', () => {
    const s = new SentenceSplitter(0, 'it')
    const md =
      '### Resi\n\n| Motivo | Resi |\n|---|---|\n| Danneggiato | 12 |\n\n' +
      '- [Apri i resi](/returns) per il dettaglio.\n'
    expect([...s.push(md), ...s.flush()].join(' ')).toBe('Resi Apri i resi per il dettaglio.')
  })

  test('the first clause can be released early', () => {
    const s = new SentenceSplitter(20, 'it')
    expect(s.push('Questa settimana i resi sono stati rimborsati, ')).toEqual(['Questa settimana i resi sono stati rimborsati,'])
    expect(s.push('in tutto, quattromila euro. Altro, poi.')).toEqual(['in tutto, quattromila euro.'])
  })

  test('personal identifiers are never spoken', () => {
    const t = speakable(
      'Cliente RSSMRA80A01H501U, IBAN IT60X0542811101000000123456, ' +
        'partita IVA 01234567890, scrivi a mario@example.test o al 333 1234567.',
      'it',
    )
    for (const x of ['RSSMRA', 'IT60X', '01234567890', 'mario@', '1234567']) expect(t).not.toContain(x)
    expect(t).toContain('il codice fiscale')
    expect(t).toContain('la partita IVA')
  })

  test('URLs are not read and a sources section is muted', () => {
    expect(speakable('Vedi https://example.test/a?b=1 per altro', 'it')).toBe('Vedi per altro')
    const s = new SentenceSplitter(0, 'it')
    const out = [...s.push('Il prezzo è salito.\n\n**Fonti:** example.test. Ancora una frase.'), ...s.flush()]
    expect(out).toEqual(['Il prezzo è salito.'])
  })
})

describe('spoken replies (it)', () => {
  test('confirmations', () => {
    expect(spokenDecision('Sì, procedi', 'it')).toBe(true)
    expect(spokenDecision('ok', 'it')).toBe(true)
    expect(spokenDecision('No, lascia stare', 'it')).toBe(false)
    expect(spokenDecision('annulla', 'it')).toBe(false)
    expect(spokenDecision('mostrami i resi', 'it')).toBeNull()
  })

  test('options by ordinal, number or words', () => {
    const o = ['Ordini per settimana', 'Per motivo', 'Confronta con la settimana scorsa']
    expect(spokenOption('la seconda', o, 'it')).toBe('Per motivo')
    expect(spokenOption('3', o, 'it')).toBe('Confronta con la settimana scorsa')
    expect(spokenOption('fammi vedere per motivo', o, 'it')).toBe('Per motivo')
    expect(spokenOption('boh', o, 'it')).toBeNull()
  })

  test('a number out of range falls back to the words, as on the server', () => {
    expect(spokenOption('la 5 per motivo', ['Totale', 'Per motivo'], 'it')).toBe('Per motivo')
  })

  test('the agent hearing its own voice is not a new question', () => {
    expect(isEcho('rimborsati quattromila euro', 'Questa settimana rimborsati quattromila euro, meno della scorsa.')).toBe(true)
    expect(isEcho('aspetta fammi vedere i resi', 'Questa settimana rimborsati quattromila euro.')).toBe(false)
    expect(isEcho('   ', 'qualcosa')).toBe(true)
  })
})

describe('English (the default)', () => {
  test('sentences, abbreviations and numbers', () => {
    const s = new SentenceSplitter()
    expect(s.push('Refunds of **4.572 €**, down 1,2% on last we')).toEqual([])
    expect(s.push('ek. See e.g. the order 1042')).toEqual(['Refunds of 4.572 €, down 1,2% on last week.'])
    expect(s.flush()).toEqual(['See e.g. the order 1042'])
  })

  test('identifiers are replaced by English words; a sources section is muted', () => {
    const t = speakable('Customer RSSMRA80A01H501U, write to mario@example.test or call 333 1234567.')
    expect(t).toBe('Customer the tax code, write to the email address or call the phone number.')
    const s = new SentenceSplitter()
    expect([...s.push('Prices rose.\n\n## References\nexample.test. More.'), ...s.flush()]).toEqual(['Prices rose.'])
  })

  test('yes, no and ordinals from the en pack', () => {
    expect(spokenDecision('Yes, go ahead')).toBe(true)
    expect(spokenDecision('go ahead')).toBe(true)
    expect(spokenDecision("Don't do that")).toBe(false)
    expect(spokenDecision('never mind')).toBe(false)
    expect(spokenDecision('show me the returns')).toBeNull()
    const o = ['Six weeks', 'By reason', 'Compare with last week']
    expect(spokenOption('the second', o)).toBe('By reason')
    expect(spokenOption('the second one', o)).toBe('By reason')
    expect(spokenOption('one, no, the third', o)).toBe('Six weeks')
    expect(spokenOption('third', o)).toBe('Compare with last week')
    expect(spokenOption('show it by reason', o)).toBe('By reason')
    expect(spokenOption('hmm', o)).toBeNull()
  })

  test('a pack of your own works too', () => {
    const custom = { ...locales.en, speech: { ...locales.en.speech, yes: ['aye'] } }
    expect(spokenDecision('aye captain', custom)).toBe(true)
    expect(localePack('it-IT').code).toBe('it')
    expect(localePack('fr').code).toBe('en')
  })
})

describe('client packs mirror the server packs', () => {
  const server = (code: string) => {
    const file = new URL(`../../../src/evokai/locales/${code}.json`, import.meta.url)
    return existsSync(file) ? (JSON.parse(readFileSync(file, 'utf8')) as { speech: Record<string, unknown> }) : null
  }
  test.each(['en', 'it'] as const)('%s', (code) => {
    const s = server(code)
    if (!s) return // the package is built outside the repository
    const c = locales[code].speech
    expect(c.yes).toEqual(s.speech.yes)
    expect(c.no).toEqual(s.speech.no)
    expect(c.ordinals).toEqual(s.speech.ordinals)
    expect(c.abbreviations).toEqual(s.speech.abbreviations)
    expect(c.redactions).toEqual(s.speech.redactions)
    expect(c.sources).toEqual(s.speech.sourcesHeadings)
  })
})
