/** French. Data only; the rules mirror the server's `src/evokai/locales/fr.json`. */
import type { LocalePack } from './types'

export const fr: LocalePack = {
  code: 'fr',
  lang: 'fr-FR',
  speech: {
    yes: ['oui', 'bien sûr', 'je confirme', 'confirmé', 'confirme', 'ok', 'd’accord', "d'accord", 'vas-y', 'allez-y', 'continue', 'fais-le', 'exact'],
    no: ['non', 'annule', 'annuler', 'laisse tomber', 'arrête', 'stop', 'ne le fais pas', 'rien'],
    ordinals: { un: 1, une: 1, premier: 1, première: 1, premiere: 1, deux: 2, deuxième: 2, deuxieme: 2, second: 2, seconde: 2, trois: 3, troisième: 3, troisieme: 3, quatre: 4, quatrième: 4, quatrieme: 4, cinq: 5, cinquième: 5, cinquieme: 5 },
    sources: ['sources', 'source', 'références'],
    abbreviations: ['ex', 'etc', 'm', 'mme', 'mlle', 'dr', 'pr', 'n', 'no', 'p', 'tél', 'vs', 'env', 'cf'],
    redactions: {
      taxCode: 'le numéro fiscal',
      iban: 'l’IBAN',
      vat: 'le numéro de TVA',
      email: 'l’adresse e-mail',
      phone: 'le numéro de téléphone',
    },
  },
  voiceText: {
    preparingVoice: (p) => (p ? `Préparation de la voix de ${p} : cela n’arrive que la première fois` : 'Préparation de la voix : cela n’arrive que la première fois'),
    voiceReady: (p) => (p ? `La voix de ${p} est prête` : 'La voix est prête'),
    recognitionUnsupported: 'La reconnaissance vocale sur l’appareil n’est pas disponible dans ce navigateur',
    recognitionDownload: 'La première utilisation télécharge la reconnaissance vocale sur l’appareil',
    micDenied: 'Le micro est bloqué : autorisez-le dans les réglages du navigateur',
    micMissing: 'Aucun micro connecté',
  },
  chatText: {
    confirmReasons: {
      'confirm.why.writes': 'Cela modifie des données',
      'confirm.why.rule': 'Une règle de cet assistant demande votre confirmation',
      'confirm.why.policy': 'La politique de sécurité demande votre confirmation',
    },
    rewriteErrors: {
      rewrite_after_write: 'Ce message ne peut plus être modifié : sa réponse a déjà modifié des données.',
      rewrite_not_last: 'Seul votre dernier message peut être modifié.',
      rewrite_nothing: 'Il n’y a encore aucun message à modifier.',
      rewrite_running: 'Attendez la réponse, ou arrêtez-la, avant de modifier votre message.',
    },
  },
  micSounds: {
    goccia: { name: 'Goutte', description: 'Deux notes sinusoïdales brèves, si–mi à l’ouverture, mi–si à la fermeture.' },
    classico: { name: 'Classique', description: 'Deux tons doux ascendants avec une longue traîne.' },
    vetro: { name: 'Verre', description: 'Une petite cloche cristalline, plus aiguë à l’ouverture.' },
    respiro: { name: 'Souffle', description: 'Un souffle filtré qui monte ou descend : pas de notes, très discret.' },
    cristallo: { name: 'Cristal', description: 'Un arpège rapide de la majeur, montant ou descendant.' },
    clic: { name: 'Clic doux', description: 'Un petit tic glissant, presque tactile.' },
  },
}
