/** German. Data only; the rules mirror the server's `src/evokai/locales/de.json`. */
import type { LocalePack } from './types'

export const de: LocalePack = {
  code: 'de',
  lang: 'de-DE',
  speech: {
    yes: ['ja', 'klar', 'sicher', 'bestätigt', 'bestätige', 'ok', 'okay', 'in ordnung', 'einverstanden', 'mach weiter', 'mach es', 'genau', 'richtig'],
    no: ['nein', 'abbrechen', 'brich ab', 'lass es', 'vergiss es', 'stopp', 'stop', 'nicht', 'nichts'],
    ordinals: { eins: 1, ein: 1, eine: 1, erste: 1, ersten: 1, erster: 1, zwei: 2, zweite: 2, zweiten: 2, zweiter: 2, drei: 3, dritte: 3, dritten: 3, dritter: 3, vier: 4, vierte: 4, vierten: 4, vierter: 4, fünf: 5, fünfte: 5, fünften: 5, fünfter: 5 },
    sources: ['quellen', 'quelle', 'referenzen', 'sources', 'source'],
    abbreviations: ['z', 'b', 'zb', 'bzw', 'usw', 'ca', 'evtl', 'ggf', 'hr', 'fr', 'dr', 'prof', 'nr', 's', 'tel', 'vgl', 'inkl'],
    redactions: {
      taxCode: 'die Steuernummer',
      iban: 'die IBAN',
      vat: 'die Umsatzsteuer-ID',
      email: 'die E-Mail-Adresse',
      phone: 'die Telefonnummer',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Die Stimme von ${p} wird vorbereitet: Das passiert nur beim ersten Mal` : 'Die Stimme wird vorbereitet: Das passiert nur beim ersten Mal'),
    voiceReady: (p) => (p ? `Die Stimme von ${p} ist bereit` : 'Die Stimme ist bereit'),
    recognitionUnsupported: 'Spracherkennung auf dem Gerät ist in diesem Browser nicht verfügbar',
    recognitionDownload: 'Bei der ersten Nutzung wird die Spracherkennung auf das Gerät geladen',
    micDenied: 'Das Mikrofon ist blockiert: Erlauben Sie es in den Browsereinstellungen',
    micMissing: 'Kein Mikrofon angeschlossen',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Dies ändert Daten',
      'confirm.why.rule': 'Eine Regel dieses Assistenten verlangt Ihre Bestätigung',
      'confirm.why.policy': 'Die Sicherheitsrichtlinie verlangt Ihre Bestätigung',
    },
    rewriteErrors: {
      rewrite_after_write: 'Diese Nachricht kann nicht mehr bearbeitet werden: Ihre Antwort hat bereits Daten geändert.',
      rewrite_not_last: 'Nur Ihre letzte Nachricht kann bearbeitet werden.',
      rewrite_nothing: 'Es gibt noch keine Nachricht zum Bearbeiten.',
      rewrite_running: 'Warten Sie die Antwort ab oder stoppen Sie sie, bevor Sie Ihre Nachricht bearbeiten.',
    },
  },
  micSounds: {
    goccia: { name: 'Tropfen', description: 'Zwei kurze Sinustöne, H–E steigend beim Öffnen, E–H fallend beim Schließen.' },
    classico: { name: 'Klassisch', description: 'Zwei weiche, steigende Töne mit langem Ausklang.' },
    vetro: { name: 'Glas', description: 'Eine kurze, kristallklare Glocke, beim Öffnen höher.' },
    respiro: { name: 'Atem', description: 'Ein gefilterter Hauch, der steigt oder fällt: keine Töne, sehr dezent.' },
    cristallo: { name: 'Kristall', description: 'Ein schnelles A-Dur-Arpeggio, steigend oder fallend.' },
    clic: { name: 'Sanfter Klick', description: 'Ein winziges gleitendes Ticken, fast fühlbar.' },
  },
}
