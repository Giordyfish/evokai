/** Spanish. Data only; the rules mirror the server's `src/evokai/locales/es.json`. */
import type { LocalePack } from './types'

export const es: LocalePack = {
  code: 'es',
  lang: 'es-ES',
  speech: {
    yes: ['sí', 'si', 'claro', 'confirmo', 'confirmado', 'confirma', 'vale', 'ok', 'de acuerdo', 'adelante', 'hazlo', 'exacto', 'correcto'],
    no: ['no', 'cancela', 'cancelar', 'déjalo', 'olvídalo', 'para', 'stop', 'no lo hagas', 'nada'],
    ordinals: { uno: 1, una: 1, primero: 1, primera: 1, primer: 1, dos: 2, segundo: 2, segunda: 2, tres: 3, tercero: 3, tercera: 3, tercer: 3, cuatro: 4, cuarto: 4, cuarta: 4, cinco: 5, quinto: 5, quinta: 5 },
    sources: ['fuentes', 'fuente', 'referencias', 'sources', 'source'],
    abbreviations: ['p', 'ej', 'etc', 'sr', 'sra', 'srta', 'dr', 'dra', 'núm', 'pág', 'tel', 'vs', 'aprox'],
    redactions: {
      taxCode: 'el número de identificación fiscal',
      iban: 'el IBAN',
      vat: 'el número de IVA',
      email: 'la dirección de correo',
      phone: 'el número de teléfono',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Preparando la voz de ${p}: solo ocurre la primera vez` : 'Preparando la voz: solo ocurre la primera vez'),
    voiceReady: (p) => (p ? `La voz de ${p} está lista` : 'La voz está lista'),
    recognitionUnsupported: 'El reconocimiento de voz en el dispositivo no está disponible en este navegador',
    recognitionDownload: 'El primer uso descarga el reconocimiento de voz en el dispositivo',
    micDenied: 'El micrófono está bloqueado: permítelo en los ajustes del navegador',
    micMissing: 'No hay ningún micrófono conectado',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Esto cambia datos',
      'confirm.why.rule': 'Una regla de este asistente pide tu confirmación',
      'confirm.why.policy': 'La política de seguridad pide tu confirmación',
    },
    rewriteErrors: {
      rewrite_after_write: 'Este mensaje ya no se puede editar: su respuesta ya ha cambiado datos.',
      rewrite_not_last: 'Solo se puede editar tu último mensaje.',
      rewrite_nothing: 'Todavía no hay ningún mensaje que editar.',
      rewrite_running: 'Espera la respuesta, o detenla, antes de editar tu mensaje.',
    },
  },
  micSounds: {
    goccia: { name: 'Gota', description: 'Dos notas sinusoidales breves, si–mi al abrir, mi–si al cerrar.' },
    classico: { name: 'Clásico', description: 'Dos tonos suaves ascendentes con una cola larga.' },
    vetro: { name: 'Vidrio', description: 'Una campanilla breve y cristalina, más aguda al abrir.' },
    respiro: { name: 'Respiro', description: 'Un soplo filtrado que sube o baja: sin notas, muy discreto.' },
    cristallo: { name: 'Cristal', description: 'Un arpegio rápido de la mayor, ascendente o descendente.' },
    clic: { name: 'Clic suave', description: 'Un pequeño tic que se desliza, casi táctil.' },
  },
}
