/** Italian. Data only; the rules mirror the server's `src/evokai/locales/it.json`. */
import type { LocalePack } from './types'

export const it: LocalePack = {
  code: 'it',
  lang: 'it-IT',
  speech: {
    yes: ['si', 'certo', 'conferma', 'confermo', 'confermato', 'ok', 'okay', 'va bene', 'procedi', 'vai', 'fallo', 'esatto'],
    no: ['no', 'annulla', 'annullato', 'lascia stare', 'ferma', 'stop', 'non farlo', 'niente'],
    ordinals: {
      uno: 1, una: 1, prima: 1, primo: 1, due: 2, seconda: 2, secondo: 2, tre: 3, terza: 3, terzo: 3,
      quattro: 4, quarta: 4, quarto: 4, cinque: 5, quinta: 5, quinto: 5,
    },
    sources: ['fonti', 'fonte', 'riferimenti', 'sources', 'source'],
    abbreviations: ['es', 'ecc', 'sig', 'dott', 'ing', 'n', 'nr', 'pag', 'tel', 'vs', 'cfr', 'art'],
    redactions: {
      taxCode: 'il codice fiscale',
      iban: 'l’IBAN',
      vat: 'la partita IVA',
      email: 'l’indirizzo email',
      phone: 'il numero di telefono',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Sto preparando la voce di ${p}: succede solo la prima volta` : 'Sto preparando la voce: succede solo la prima volta'),
    voiceReady: (p) => (p ? `La voce di ${p} è pronta` : 'La voce è pronta'),
    recognitionUnsupported: 'Il riconoscimento vocale sul dispositivo non è disponibile in questo browser',
    recognitionDownload: 'Al primo uso scarica il riconoscimento vocale sul dispositivo',
    micDenied: 'Il microfono non è consentito: abilitalo nelle impostazioni del browser',
    micMissing: 'Nessun microfono collegato',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Questa operazione cambia dei dati',
      'confirm.why.rule': 'Una regola di questo assistente chiede la tua conferma',
      'confirm.why.policy': 'La politica di sicurezza chiede la tua conferma',
    },
    rewriteErrors: {
      rewrite_after_write: 'Questo messaggio non si può più modificare: la risposta ha già cambiato dei dati.',
      rewrite_not_last: 'Puoi modificare solo il tuo ultimo messaggio.',
      rewrite_nothing: 'Non c’è ancora un messaggio da modificare.',
      rewrite_running: 'Aspetta la risposta, o interrompila, prima di modificare il messaggio.',
    },
  },
  micSounds: {
    goccia: { name: 'Goccia', description: 'Due note sinusoidali brevi, si–mi a salire, mi–si a scendere.' },
    classico: { name: 'Classico', description: 'Due toni morbidi a salire, con una coda lunga.' },
    vetro: { name: 'Vetro', description: 'Una campanella breve e cristallina, più acuta in accensione.' },
    respiro: { name: 'Respiro', description: 'Un soffio filtrato che sale o scende: niente note, molto discreto.' },
    cristallo: { name: 'Cristallo', description: 'Un arpeggio veloce di la maggiore, a salire o a scendere.' },
    clic: { name: 'Clic morbido', description: 'Un piccolo «tic» con un tono che scivola, quasi tattile.' },
  },
}
