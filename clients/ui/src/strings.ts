/**
 * Every text the components show, in English and Italian. Pass `strings` (a partial object) to change any of them;
 * `{title}` is the assistant's name and `{app}` the application's.
 */

export interface Strings {
  /** The launcher. */
  open: string
  close: string
  /** The panel's subtitle when idle. */
  tagline: string
  working: string
  thinking: string
  speaking: string
  listening: string
  /** Voice mode: the agent stopped because the user talked over it, and listens. */
  interrupted: string
  history: string
  noHistory: string
  untitled: string
  newChat: string
  placeholder: string
  placeholderDictating: string
  placeholderVoice: string
  placeholderVoiceSpeaking: string
  message: string
  send: string
  stop: string
  dictate: string
  stopDictation: string
  voiceMode: string
  voiceModeHint: string
  leaveVoice: string
  recognitionUnsupported: string
  recognitionDownloadable: string
  recognitionDownloading: string
  recognitionReady: string
  recognitionFailed: string
  /** Voice mode without any voice on this device to read the answers. */
  speechUnavailable: string
  unreachable: string
  /** Attachments: the paper clip, a preview's remove button, refusals (`{name}`, `{max}`, `{n}`). */
  attach: string
  removeFile: string
  fileTooLarge: string
  fileKind: string
  tooManyFiles: string
  fileUnsupported: string
  fileUnreadable: string
  filesFull: string
  fileRefused: string
  helpful: string
  notHelpful: string
  toolFailed: string
  toolRunning: string
  /** Knowledge: the sources under an answer, a passage's page, the search tool's label. */
  sources: string
  sourcePage: string
  searching: string
  openSource: string
  /** Ask cards. */
  other: string
  yourAnswer: string
  writeAnswer: string
  submit: string
  confirmChoice: string
  /** Confirm cards. */
  confirmTitle: string
  /** The title when nothing waiting changes data (an ask rule asked). */
  confirmAskTitle: string
  /** Editing the last question (it replaces the last turn). */
  edit: string
  /** Regenerate the last answer. */
  regenerate: string
  editing: string
  cancelEdit: string
  confirmed: string
  cancelled: string
  closed: string
  confirm: string
  cancel: string
  /** Usage (shares only). */
  usageTitle: string
  usageOut: string
  usageToday: string
  usageMonth: string
  /** A weekly cap of the person. */
  usageWeek: string
  usageApp: string
  usageRenews: string
  /** When the weekly or monthly share that limits most renews: `{when}`. */
  usageRenewsAt: string
  usageBack: string
  /** A cap other than the month is reached. */
  usageLimitOut: string
  /** `{when}`: the cap's reset, as a date and time. */
  usageBackAt: string
  usageAria: string
  usageOutAria: string
  /** Voice stage. */
  voiceDialog: string
  keyboard: string
  mute: string
  unmute: string
  micOff: string
  endVoice: string
  talkOver: string
  talkOverOn: string
  talkOverOff: string
  details: string
  /** Voice mode in the panel: minimize it into the launcher orb, and bring it back. */
  minimize: string
  minimizeHint: string
  restore: string
  restoreHint: string
  /** The minimized bubble: its chart chip's action, and the group of buttons over the orb. */
  openChart: string
  voiceActions: string
  /** Charts and diagrams. */
  chartOther: string
  current: string
  total: string
  stepDone: string
  stepCurrent: string
  stepTodo: string
  differences: (n: number) => string
  same: string
  differs: string
  /** The header menu. */
  menu: string
  /** Memory: the menu entry and the view of what the agents remember. */
  memoryMenu: string
  memoryTitle: string
  memoryBack: string
  memoryIntro: string
  /** The groups, by kind: how the person likes answers, their work, finished conversations. */
  memoryPreference: string
  memorySemantic: string
  memoryEpisodic: string
  memoryEmpty: string
  memoryLoading: string
  memoryLoadFailed: string
  retry: string
  memoryEditedByYou: string
  memoryEdit: string
  memoryEditLabel: string
  memoryDelete: string
  memoryDeleteLabel: string
  memorySave: string
  /** The service refused an edit (instructions, secrets or personal data). */
  memoryRefused: string
  memoryFailed: string
  memoryForgetAll: string
  memoryForgetAllConfirm: string
  /** Memory is off for the person (they said no, or have not said yes). */
  memoryOff: string
  memoryTurnOn: string
  memoryTurnOff: string
  memoryTurnOffConfirm: string
  memoryTurnOffAction: string
  /** The one-time consent question. */
  memoryAsk: string
  memoryAskLabel: string
  memoryAskYes: string
  memoryAskNo: string
  memoryOnNote: string
  memoryOffNote: string
  /** Connections: the menu entry, the view of the person's connected accounts and the card a tool shows. */
  connectionsMenu: string
  connectionsTitle: string
  connectionsIntro: string
  connectionsEmpty: string
  connectionsLoading: string
  connectionsLoadFailed: string
  /** A provider's state. */
  connectionConnected: string
  connectionExpired: string
  connectionNotConnected: string
  /** «as {account}». */
  connectionAccount: string
  /** «last used {when}». */
  connectionLastUsed: string
  /** What the agent may use: «Mail, calendar, files». */
  connectionFamilies: string
  connectionFamilyMail: string
  connectionFamilyCalendar: string
  connectionFamilyFiles: string
  connect: string
  /** A connection that expired: connect it again. */
  reconnect: string
  disconnect: string
  disconnectConfirm: string
  /** While the provider's window is open. */
  connectionWaiting: string
  connectionFailed: string
  /** The card a tool shows when its provider is not connected. */
  connectCardTitle: string
  connectCardText: string
  connectCardDone: string
}

export const en: Strings = {
  open: 'Open {title}',
  close: 'Close',
  tagline: 'Your assistant',
  working: 'Working…',
  thinking: 'Thinking…',
  speaking: 'Speaking…',
  listening: 'Listening…',
  interrupted: 'Interrupted, listening…',
  history: 'Conversations',
  noHistory: 'No conversations yet.',
  untitled: 'Conversation',
  newChat: 'New conversation',
  placeholder: 'Ask {title}…',
  placeholderDictating: 'Speak, I am writing…',
  placeholderVoice: 'Listening…',
  placeholderVoiceSpeaking: 'Speak to interrupt…',
  message: 'Message for {title}',
  send: 'Send',
  stop: 'Stop',
  dictate: 'Dictate',
  stopDictation: 'Stop dictation',
  voiceMode: 'Voice mode',
  voiceModeHint: 'Voice mode: you speak and {title} answers aloud too',
  leaveVoice: 'Leave voice mode',
  recognitionUnsupported: 'On-device speech recognition is not available in this browser',
  recognitionDownloadable: 'The first time, the speech recognition for your language downloads to this device',
  recognitionDownloading: 'Downloading speech recognition to this device. Only the first time; it can take a minute…',
  recognitionReady: 'Speech recognition is ready: you can talk.',
  recognitionFailed: 'Speech recognition did not download. Try again or type.',
  speechUnavailable: 'This device has no voice in your language: answers stay written. Install one in the system settings.',
  unreachable: 'The assistant cannot be reached.',
  attach: 'Attach files',
  removeFile: 'Remove {name}',
  fileTooLarge: '{name} is too large (at most {max}).',
  fileKind: '{name}: this kind of file is not accepted.',
  tooManyFiles: 'At most {n} files per message.',
  fileUnsupported: 'The assistant cannot read images.',
  fileUnreadable: 'A file cannot be read: it has no text.',
  filesFull: 'This conversation cannot take more files.',
  fileRefused: 'A file was not accepted.',
  helpful: 'Helpful answer',
  notHelpful: 'Not helpful',
  toolFailed: 'failed',
  toolRunning: '…',
  sources: 'Sources',
  sourcePage: 'p. {page}',
  searching: 'Searching the documents',
  openSource: 'Open the source: {title}',
  other: 'Other…',
  yourAnswer: 'Your answer',
  writeAnswer: 'Write the answer',
  submit: 'Send',
  confirmChoice: 'Confirm',
  confirmTitle: 'Confirm this change?',
  confirmAskTitle: 'Go ahead?',
  edit: 'Edit',
  regenerate: 'Regenerate the answer',
  editing: 'Editing your last message',
  cancelEdit: 'Cancel',
  confirmed: 'Change confirmed',
  cancelled: 'Change cancelled',
  closed: 'Request closed',
  confirm: 'Confirm',
  cancel: 'Cancel',
  usageTitle: 'Use of the assistant',
  usageOut: 'Monthly limit reached',
  usageToday: 'Today, you',
  usageMonth: 'This month, you',
  usageWeek: 'This week, you',
  usageApp: 'This month, {app}',
  usageRenews: 'Today’s share renews every night.',
  usageRenewsAt: 'Renews {when}.',
  usageBack: 'Available again on the first day of the month.',
  usageLimitOut: 'Limit reached',
  usageBackAt: 'Available again {when}.',
  usageAria: 'Use today: {pct}',
  usageOutAria: 'Monthly limit reached',
  voiceDialog: '{title}, voice mode',
  keyboard: 'Back to the keyboard',
  mute: 'Mute the microphone',
  unmute: 'Unmute the microphone',
  micOff: 'Microphone off',
  endVoice: 'End voice mode',
  talkOver: 'Interrupt by speaking',
  talkOverOn: 'If you speak while {title} speaks, it stops and listens',
  talkOverOff: 'Turns: the microphone opens when {title} has finished speaking',
  details: 'Details',
  minimize: 'Minimize',
  minimizeHint: 'Minimize: the voice conversation goes on in the orb',
  restore: 'Open again',
  restoreHint: 'Open the panel again',
  openChart: 'Open',
  voiceActions: 'Voice mode',
  chartOther: 'Other',
  current: 'Current',
  total: 'total',
  stepDone: 'done',
  stepCurrent: 'in progress',
  stepTodo: 'to do',
  differences: (n) => `${n} ${n === 1 ? 'difference' : 'differences'}`,
  same: 'They match',
  differs: 'different',
  menu: 'More',
  memoryMenu: 'What {title} remembers',
  memoryTitle: 'What {title} remembers',
  memoryBack: 'Back to the conversation',
  memoryIntro: 'What {title} remembers about you helps it answer better. You can change or delete it at any time.',
  memoryPreference: 'How you like answers',
  memorySemantic: 'About your work',
  memoryEpisodic: 'Past conversations',
  memoryEmpty: 'Nothing yet. As you talk, {title} keeps here what helps it help you.',
  memoryLoading: 'Loading…',
  memoryLoadFailed: 'What {title} remembers cannot be loaded right now.',
  retry: 'Try again',
  memoryEditedByYou: 'edited by you',
  memoryEdit: 'Edit',
  memoryEditLabel: 'Edit this memory',
  memoryDelete: 'Delete',
  memoryDeleteLabel: 'Delete this memory',
  memorySave: 'Save',
  memoryRefused: 'This cannot be kept: a memory may not contain instructions, passwords or personal data.',
  memoryFailed: 'That did not work. Try again.',
  memoryForgetAll: 'Forget everything',
  memoryForgetAllConfirm: 'Forget everything that is remembered about you? This cannot be undone.',
  memoryOff: 'Memory is off for you: {title} does not remember anything from one conversation to the next.',
  memoryTurnOn: 'Turn on memory',
  memoryTurnOff: 'Turn off memory',
  memoryTurnOffConfirm: 'Turn off memory? Everything remembered about you is deleted, and nothing new is kept.',
  memoryTurnOffAction: 'Turn off and delete',
  memoryAsk: 'May I remember things about your work to help you better? You can see, change or delete them at any time.',
  memoryAskLabel: 'Memory',
  memoryAskYes: 'Yes',
  memoryAskNo: 'No thanks',
  memoryOnNote: 'Thank you. You can see what is remembered from the menu.',
  memoryOffNote: 'All right: nothing will be remembered.',
  connectionsMenu: 'Connections',
  connectionsTitle: 'Connections',
  connectionsIntro: 'Accounts {title} can use on your behalf, with your permissions. It always asks before it writes or creates anything.',
  connectionsEmpty: 'There is nothing to connect here.',
  connectionsLoading: 'Loading…',
  connectionsLoadFailed: 'Your connections cannot be loaded right now.',
  connectionConnected: 'Connected',
  connectionExpired: 'Expired: connect again',
  connectionNotConnected: 'Not connected',
  connectionAccount: 'as {account}',
  connectionLastUsed: 'last used {when}',
  connectionFamilies: 'Can use: {families}',
  connectionFamilyMail: 'mail',
  connectionFamilyCalendar: 'calendar',
  connectionFamilyFiles: 'files',
  connect: 'Connect',
  reconnect: 'Connect again',
  disconnect: 'Disconnect',
  disconnectConfirm: 'Disconnect {provider}? {title} will no longer reach your account, until you connect it again.',
  connectionWaiting: 'Finish in the window that opened, then come back here.',
  connectionFailed: 'That did not work. Try again.',
  connectCardTitle: 'Connect {provider}',
  connectCardText: 'To do this, {title} needs access to your {provider} account.',
  connectCardDone: '{provider} is connected: ask again.',
}

export const it: Strings = {
  open: 'Apri {title}',
  close: 'Chiudi',
  tagline: 'Il tuo assistente',
  working: 'Sto lavorando…',
  thinking: 'Sto pensando…',
  speaking: 'Sto parlando…',
  listening: 'Ti ascolto…',
  interrupted: 'Interrotta, ti ascolto…',
  history: 'Conversazioni',
  noHistory: 'Nessuna conversazione.',
  untitled: 'Conversazione',
  newChat: 'Nuova conversazione',
  placeholder: 'Chiedi a {title}…',
  placeholderDictating: 'Parla, scrivo io…',
  placeholderVoice: 'Ti ascolto…',
  placeholderVoiceSpeaking: 'Parla per interrompere…',
  message: 'Messaggio per {title}',
  send: 'Invia',
  stop: 'Ferma',
  dictate: 'Detta',
  stopDictation: 'Ferma la dettatura',
  voiceMode: 'Modalità vocale',
  voiceModeHint: 'Modalità vocale: parli e {title} ti risponde anche a voce',
  leaveVoice: 'Esci dalla modalità vocale',
  recognitionUnsupported: 'Il riconoscimento vocale sul dispositivo non è disponibile in questo browser',
  recognitionDownloadable: 'Al primo uso scarica sul dispositivo il riconoscimento vocale della tua lingua',
  recognitionDownloading: 'Scarico il riconoscimento vocale sul dispositivo. Solo la prima volta, può volerci un minuto…',
  recognitionReady: 'Riconoscimento vocale pronto: puoi parlare.',
  recognitionFailed: 'Il riconoscimento vocale non si è scaricato. Riprova o scrivi.',
  speechUnavailable: 'Su questo dispositivo non c’è una voce in italiano: le risposte restano scritte. Puoi installarla nelle impostazioni di sistema.',
  unreachable: 'L’assistente non è raggiungibile.',
  attach: 'Allega file',
  removeFile: 'Rimuovi {name}',
  fileTooLarge: '{name} è troppo grande (al massimo {max}).',
  fileKind: '{name}: questo tipo di file non è accettato.',
  tooManyFiles: 'Al massimo {n} file per messaggio.',
  fileUnsupported: 'L’assistente non legge le immagini.',
  fileUnreadable: 'Un file non si può leggere: non contiene testo.',
  filesFull: 'Questa conversazione non accetta altri file.',
  fileRefused: 'Un file non è stato accettato.',
  helpful: 'Risposta utile',
  notHelpful: 'Risposta non utile',
  toolFailed: 'non riuscito',
  toolRunning: '…',
  sources: 'Fonti',
  sourcePage: 'p. {page}',
  searching: 'Ricerca nei documenti',
  openSource: 'Apri la fonte: {title}',
  other: 'Altro…',
  yourAnswer: 'La tua risposta',
  writeAnswer: 'Scrivi la risposta',
  submit: 'Invia',
  confirmChoice: 'Conferma',
  confirmTitle: 'Confermi questa modifica?',
  confirmAskTitle: 'Procedo?',
  edit: 'Modifica',
  regenerate: 'Rigenera la risposta',
  editing: 'Modifichi il tuo ultimo messaggio',
  cancelEdit: 'Annulla',
  confirmed: 'Modifica confermata',
  cancelled: 'Modifica annullata',
  closed: 'Richiesta chiusa',
  confirm: 'Conferma',
  cancel: 'Annulla',
  usageTitle: 'Uso dell’assistente',
  usageOut: 'Limite del mese raggiunto',
  usageToday: 'Oggi, tu',
  usageMonth: 'Questo mese, tu',
  usageWeek: 'Questa settimana, tu',
  usageApp: 'Questo mese, {app}',
  usageRenews: 'La quota di oggi si rinnova ogni notte.',
  usageRenewsAt: 'Si rinnova {when}.',
  usageBack: 'Torna disponibile il primo giorno del mese.',
  usageLimitOut: 'Limite raggiunto',
  usageBackAt: 'Torna disponibile {when}.',
  usageAria: 'Uso di oggi: {pct}',
  usageOutAria: 'Limite del mese raggiunto',
  voiceDialog: '{title}, modalità vocale',
  keyboard: 'Torna alla tastiera',
  mute: 'Silenzia il microfono',
  unmute: 'Riattiva il microfono',
  micOff: 'Microfono spento',
  endVoice: 'Termina la modalità vocale',
  talkOver: 'Interrompi parlando',
  talkOverOn: 'Se parli mentre {title} parla, si ferma e ti ascolta',
  talkOverOff: 'A turni: il microfono si apre quando {title} ha finito di parlare',
  details: 'Dettagli',
  minimize: 'Riduci',
  minimizeHint: 'Riduci: la conversazione a voce continua nella sfera',
  restore: 'Riapri',
  restoreHint: 'Riapri il pannello',
  openChart: 'Apri',
  voiceActions: 'Modalità vocale',
  chartOther: 'Altro',
  current: 'Attuale',
  total: 'totale',
  stepDone: 'fatto',
  stepCurrent: 'in corso',
  stepTodo: 'da fare',
  differences: (n) => `${n} ${n === 1 ? 'differenza' : 'differenze'}`,
  same: 'Coincidono',
  differs: 'diverso',
  menu: 'Altro',
  memoryMenu: 'Cosa ricordo di te',
  memoryTitle: 'Cosa ricordo di te',
  memoryBack: 'Torna alla conversazione',
  memoryIntro: 'Quello che ricordo di te mi aiuta a risponderti meglio. Puoi modificarlo o cancellarlo quando vuoi.',
  memoryPreference: 'Come preferisci le risposte',
  memorySemantic: 'Sul tuo lavoro',
  memoryEpisodic: 'Conversazioni passate',
  memoryEmpty: 'Ancora niente. Mentre parliamo, qui terrò quello che mi aiuta ad aiutarti.',
  memoryLoading: 'Caricamento…',
  memoryLoadFailed: 'Al momento non riesco a caricare quello che ricordo.',
  retry: 'Riprova',
  memoryEditedByYou: 'modificato da te',
  memoryEdit: 'Modifica',
  memoryEditLabel: 'Modifica questo ricordo',
  memoryDelete: 'Elimina',
  memoryDeleteLabel: 'Elimina questo ricordo',
  memorySave: 'Salva',
  memoryRefused: 'Non posso ricordarlo: un ricordo non può contenere istruzioni, password o dati personali.',
  memoryFailed: 'Non è riuscito. Riprova.',
  memoryForgetAll: 'Dimentica tutto',
  memoryForgetAllConfirm: 'Dimentico tutto quello che ricordo di te? Non si può annullare.',
  memoryOff: 'La memoria è disattivata per te: non ricordo nulla da una conversazione all’altra.',
  memoryTurnOn: 'Attiva la memoria',
  memoryTurnOff: 'Disattiva la memoria',
  memoryTurnOffConfirm: 'Disattivo la memoria? Cancello tutto quello che ricordo di te e smetto di ricordare.',
  memoryTurnOffAction: 'Disattiva e cancella',
  memoryAsk: 'Posso ricordare alcune cose sul tuo lavoro per aiutarti meglio? Puoi vederle, modificarle o cancellarle quando vuoi.',
  memoryAskLabel: 'Memoria',
  memoryAskYes: 'Sì',
  memoryAskNo: 'No, grazie',
  memoryOnNote: 'Grazie. Trovi quello che ricordo nel menu.',
  memoryOffNote: 'D’accordo: non ricorderò nulla.',
  connectionsMenu: 'Collegamenti',
  connectionsTitle: 'Collegamenti',
  connectionsIntro: 'Gli account che {title} può usare per tuo conto, con i tuoi permessi. Prima di scrivere o creare qualcosa ti chiede sempre conferma.',
  connectionsEmpty: 'Non c’è nulla da collegare qui.',
  connectionsLoading: 'Caricamento…',
  connectionsLoadFailed: 'Al momento non riesco a caricare i tuoi collegamenti.',
  connectionConnected: 'Collegato',
  connectionExpired: 'Scaduto: collegalo di nuovo',
  connectionNotConnected: 'Non collegato',
  connectionAccount: 'come {account}',
  connectionLastUsed: 'ultimo uso {when}',
  connectionFamilies: 'Può usare: {families}',
  connectionFamilyMail: 'posta',
  connectionFamilyCalendar: 'calendario',
  connectionFamilyFiles: 'file',
  connect: 'Collega',
  reconnect: 'Collega di nuovo',
  disconnect: 'Scollega',
  disconnectConfirm: 'Scollegare {provider}? {title} non potrà più accedere al tuo account finché non lo colleghi di nuovo.',
  connectionWaiting: 'Completa nella finestra che si è aperta, poi torna qui.',
  connectionFailed: 'Non ha funzionato. Riprova.',
  connectCardTitle: 'Collega {provider}',
  connectCardText: 'Per farlo, {title} ha bisogno di accedere al tuo account {provider}.',
  connectCardDone: '{provider} è collegato: chiedimelo di nuovo.',
}

export const strings: Record<'en' | 'it', Strings> = { en, it }

/** The strings of a locale (unknown codes fall back to English) with the overrides applied. */
export function stringsFor(locale: string | null | undefined, overrides?: Partial<Strings> | null): Strings {
  const base = strings[(locale ?? 'en').slice(0, 2) as 'en' | 'it'] ?? en
  return { ...base, ...(overrides ?? {}) }
}

/** Replace `{name}` placeholders. */
export function fill(text: string, values: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (m, k: string) => values[k] ?? m)
}
