/** Romanian. Data only; the rules mirror the server's `src/evokai/locales/ro.json`. */
import type { LocalePack } from './types'

export const ro: LocalePack = {
  code: 'ro',
  lang: 'ro-RO',
  speech: {
    yes: ['da', 'sigur', 'confirm', 'confirmat', 'confirmă', 'ok', 'bine', 'de acord', 'continuă', 'fă-o', 'exact', 'corect'],
    no: ['nu', 'anulează', 'anulat', 'lasă', 'las-o baltă', 'oprește', 'stop', 'nu face asta', 'nimic'],
    ordinals: { unu: 1, una: 1, primul: 1, prima: 1, doi: 2, două: 2, doua: 2, 'al doilea': 2, trei: 3, treia: 3, 'al treilea': 3, patru: 4, patra: 4, 'al patrulea': 4, cinci: 5, cincea: 5, 'al cincilea': 5 },
    sources: ['surse', 'sursă', 'sursa', 'referințe', 'sources', 'source'],
    abbreviations: ['ex', 'etc', 'dl', 'dna', 'dr', 'prof', 'nr', 'pag', 'tel', 'vs', 'aprox', 'str'],
    redactions: {
      taxCode: 'codul fiscal',
      iban: 'IBAN-ul',
      vat: 'codul de TVA',
      email: 'adresa de e-mail',
      phone: 'numărul de telefon',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Se pregătește vocea lui ${p}: se întâmplă doar prima dată` : 'Se pregătește vocea: se întâmplă doar prima dată'),
    voiceReady: (p) => (p ? `Vocea lui ${p} este pregătită` : 'Vocea este pregătită'),
    recognitionUnsupported: 'Recunoașterea vocală pe dispozitiv nu este disponibilă în acest browser',
    recognitionDownload: 'La prima utilizare se descarcă recunoașterea vocală pe dispozitiv',
    micDenied: 'Microfonul este blocat: permiteți-l în setările browserului',
    micMissing: 'Niciun microfon conectat',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Aceasta modifică date',
      'confirm.why.rule': 'O regulă a acestui asistent cere confirmarea dumneavoastră',
      'confirm.why.policy': 'Politica de securitate cere confirmarea dumneavoastră',
    },
    rewriteErrors: {
      rewrite_after_write: 'Acest mesaj nu mai poate fi editat: răspunsul lui a modificat deja date.',
      rewrite_not_last: 'Doar ultimul dumneavoastră mesaj poate fi editat.',
      rewrite_nothing: 'Încă nu există niciun mesaj de editat.',
      rewrite_running: 'Așteptați răspunsul sau opriți-l înainte de a vă edita mesajul.',
    },
  },
  micSounds: {
    goccia: { name: 'Picătură', description: 'Două note sinusoidale scurte, si–mi la deschidere, mi–si la închidere.' },
    classico: { name: 'Clasic', description: 'Două tonuri moi, ascendente, cu o coadă lungă.' },
    vetro: { name: 'Sticlă', description: 'Un clopoțel scurt și cristalin, mai înalt la deschidere.' },
    respiro: { name: 'Respirație', description: 'O suflare filtrată care urcă sau coboară: fără note, foarte discretă.' },
    cristallo: { name: 'Cristal', description: 'Un arpegiu rapid în la major, ascendent sau descendent.' },
    clic: { name: 'Clic moale', description: 'Un mic tic alunecat, aproape tactil.' },
  },
}
