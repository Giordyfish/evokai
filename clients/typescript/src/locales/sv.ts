/** Swedish. Data only; the rules mirror the server's `src/evokai/locales/sv.json`. */
import type { LocalePack } from './types'

export const sv: LocalePack = {
  code: 'sv',
  lang: 'sv-SE',
  speech: {
    yes: ['ja', 'visst', 'absolut', 'bekräftar', 'bekräftat', 'bekräfta', 'ok', 'okej', 'kör', 'fortsätt', 'gör det', 'precis', 'stämmer'],
    no: ['nej', 'avbryt', 'avbrutet', 'strunta i det', 'glöm det', 'stopp', 'stop', 'gör det inte', 'ingenting'],
    ordinals: { ett: 1, en: 1, första: 1, forsta: 1, två: 2, tva: 2, andra: 2, tre: 3, tredje: 3, fyra: 4, fjärde: 4, fjarde: 4, fem: 5, femte: 5 },
    sources: ['källor', 'källa', 'referenser', 'sources', 'source'],
    abbreviations: ['t.ex', 'bl.a', 'osv', 'm.m', 'ca', 'dvs', 'nr', 's', 'tel', 'vs', 'kl', 'jfr'],
    redactions: {
      taxCode: 'personnumret',
      iban: 'IBAN-numret',
      vat: 'momsnumret',
      email: 'e-postadressen',
      phone: 'telefonnumret',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Förbereder rösten för ${p}: det händer bara första gången` : 'Förbereder rösten: det händer bara första gången'),
    voiceReady: (p) => (p ? `Rösten för ${p} är klar` : 'Rösten är klar'),
    recognitionUnsupported: 'Taligenkänning på enheten är inte tillgänglig i den här webbläsaren',
    recognitionDownload: 'Första användningen laddar ner taligenkänning till enheten',
    micDenied: 'Mikrofonen är blockerad: tillåt den i webbläsarens inställningar',
    micMissing: 'Ingen mikrofon ansluten',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Det här ändrar data',
      'confirm.why.rule': 'En regel för den här assistenten ber om din bekräftelse',
      'confirm.why.policy': 'Säkerhetspolicyn ber om din bekräftelse',
    },
    rewriteErrors: {
      rewrite_after_write: 'Det här meddelandet kan inte längre redigeras: svaret har redan ändrat data.',
      rewrite_not_last: 'Bara ditt senaste meddelande kan redigeras.',
      rewrite_nothing: 'Det finns inget meddelande att redigera än.',
      rewrite_running: 'Vänta på svaret, eller stoppa det, innan du redigerar ditt meddelande.',
    },
  },
  micSounds: {
    goccia: { name: 'Droppe', description: 'Två korta sinustoner, H–E stigande vid öppning, E–H fallande vid stängning.' },
    classico: { name: 'Klassisk', description: 'Två mjuka stigande toner med lång efterklang.' },
    vetro: { name: 'Glas', description: 'En kort kristallklar klocka, högre vid öppning.' },
    respiro: { name: 'Andetag', description: 'En filtrerad pust som stiger eller sjunker: inga toner, mycket diskret.' },
    cristallo: { name: 'Kristall', description: 'Ett snabbt A-dur-arpeggio, stigande eller fallande.' },
    clic: { name: 'Mjukt klick', description: 'Ett litet glidande tick, nästan kännbart.' },
  },
}
