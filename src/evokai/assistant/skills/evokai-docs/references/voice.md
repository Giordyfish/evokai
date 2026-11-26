# Server speech

Evokai can speak and listen on the server, with the speech engines your deployment offers: a page that wants a voice
better than the browser's, word times for karaoke subtitles, cues timed for an animated character of its own, or
recognition on devices without one, asks Evokai instead of the browser. Browser speech stays the default and costs
nothing; server speech is opt-in, granted per application and counted in its caps.

Three ways in, all with the session token, all mounted with `EVOKAI_VOICE_ENABLED=true` (the `voice` extra):

| | Route | What it does |
|---|---|---|
| Synthesis | `POST /v1/session/speech` | speaks a text or one `say` segment; streams audio and timings |
| Transcription | `POST /v1/session/transcribe` | transcribes an audio file sent as the request body |
| Streaming recognition | `WS /v1/session/transcribe/stream` | transcribes the microphone as the person speaks |

The WebRTC voice sessions (architecture) use the same engines.

## Engines

Every engine is a [Pipecat](https://docs.pipecat.ai) service; Evokai adds the timings, the grants, the caps and the data
protection checks.

| Engine | Speech (`tts`) | Recognition (`stt`) | Install | Credentials and settings |
|---|---|---|---|---|
| `elevenlabs` | HTTP with timestamps: word times; speech tags with an `eleven_v3` model | Scribe, one utterance at a time; Scribe realtime for streaming | nothing more | `ELEVENLABS_API_KEY`, `EVOKAI_ELEVENLABS_TTS_MODEL` (default `eleven_flash_v2_5`), `EVOKAI_ELEVENLABS_VOICE`, `EVOKAI_ELEVENLABS_URL` (the EU residency host) |
| `openai` | `gpt-4o-mini-tts` (word times estimated) | transcription models; realtime transcription for streaming | nothing more | `OPENAI_API_KEY`, `EVOKAI_OPENAI_TTS_MODEL`, `EVOKAI_OPENAI_STT_MODEL`, `EVOKAI_OPENAI_SPEECH_URL` |
| `azure` | Speech SDK: word boundaries and visemes | real-time recognition | `pipecat-ai[azure]` | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` |
| `piper` | a Piper server of your own (times estimated) | | `pipecat-ai[piper]`, and a `piper-tts[http]` server | `EVOKAI_PIPER_URL` |
| `kokoro` | in process (times estimated) | | `pipecat-ai[kokoro]` | |
| `whisper` | | faster-whisper in process | `pipecat-ai[whisper]` | `EVOKAI_WHISPER_MODEL` |
| `fake` | silence with word times and visemes | scripted phrases | | tests and local runs |

Any other Pipecat service is an engine too: give `module:factory` as its name. The deployment's default engines are
`EVOKAI_VOICE_TTS` and `EVOKAI_VOICE_STT`; `EVOKAI_VOICE_TTS_ENGINES` and `EVOKAI_VOICE_STT_ENGINES` (JSON lists) add
the ones a session token may choose. Each engine has an entry in the [provider catalog](data-protection.md) with its
residency, retention and price, so the application's data protection profile judges it and its usage is priced. The
ElevenLabs price is that of its Flash model: a deployment on `eleven_v3` (for speech tags) sets its own price with
`EVOKAI_PROVIDERS_FILE`, or the caps count half of what is spent.

Each request builds its engine anew: in-process engines (`kokoro`, `whisper`) load their model every time, so they
suit a few sessions; for more, run the model as a server of its own (Piper's HTTP server) or use a hosted engine. Each
segment is synthesised on its own, without the text around it as context.

## Choosing, granting, paying

- A session token chooses its engines with `speech: {"tts": "elevenlabs", "stt": "openai"}` (400 `speech_engine` for
  an engine the deployment does not offer); without it the defaults apply. The voice is the token's `voice`, else the
  agent's `voice.voice_id`, else the engine's default.
- Server speech needs the application's `voice: server` grant; a recognition engine its `stt` grant; the token's voice
  its `tts` grant (`server/<voice>`). There is no grant per speech engine: an application granted server speech may
  use every speech engine the deployment offers, voice by voice.
- Every engine must pass the application's data protection profile (403 `data_protection`).
- Characters synthesised and seconds transcribed are recorded as usage (kinds `tts` and `stt`) at the catalog's price,
  and the caps apply before each request (429 `limit`). Browsers only ever see shares of the caps.
- The text is made speakable and its personal identifiers redacted exactly as a `say` segment is.

## Synthesis

```http
POST /v1/session/speech
Authorization: Bearer <session token>
Content-Type: application/json

{"turnId": "t_123", "index": 0, "format": "pcm", "sampleRate": 24000}
```

The body names a `say` segment of this conversation (`turnId`, `index`; 404 for any other) or a `text` (up to
`EVOKAI_SPEECH_MAX_CHARS`, 2,000 by default; Markdown and [inline markup](markup.md) are handled as in an answer).
`format` is `pcm` (16-bit little-endian mono at `sampleRate`: 16000, 22050, 24000, 44100 or 48000) or `opus` (Ogg Opus at
48 kHz). The answer is a stream of Server-Sent Events, chosen because both clients already read them and a browser
can start playing before the engine has finished:

| Event | Data |
|---|---|
| `start` | `engine`, `voice`, `format`, `sampleRate`, `channels`, `text` (the subtitle), `spoken` (what is said) |
| `audio` | `data` (base64), `ms` (where the chunk starts; null for the last Opus pages) |
| `word` | `text`, `at` and `length` (characters of `spoken`), `start` (ms) |
| `mark` | a cue of the segment: `name`, `arg`, `at`, `time` (ms): it fires with the first word at or after it |
| `viseme` | `id` (the engine's viseme id), `time` (ms), from engines that give them |
| `done` | `durationMs`, `characters`, `estimated` (the engine gave no word times: they are spread by the text's length) |
| `error` | `code`, `message` |

A word ends where the next one starts (the last at `durationMs`); per-character times follow from `at` and `length`.
A segment whose engine performs speech tags (`tts_caps`, an ElevenLabs v3 model) is spoken in its `tagged` form.

## Transcription

```http
POST /v1/session/transcribe?language=it-IT&hint=Evokai,%20Pipecat
Content-Type: audio/webm

<the recording>
```

WAV, WebM, Ogg, MP3 or M4A, up to `EVOKAI_TRANSCRIBE_MAX_BYTES` (10 MB, else 413 `too_large` or `audio_too_large`) and
`EVOKAI_TRANSCRIBE_MAX_SECONDS` (120, else 400 `audio_too_long`); anything else is 400 `audio_invalid`. The answer is
`{text, durationMs, engine}`. `language` defaults to the session's; `hint` is an optional
[vocabulary hint](#vocabulary-hints), and phrases recognisers [make up from silence](#made-up-phrases) are left out
of `text`.

## Streaming recognition

The socket authenticates with the session token in its subprotocols, never in the URL: the client offers `evokai.v1`
and `evokai.token.<session token>`, and the server accepts `evokai.v1`. A refused socket is accepted and closed at
once with a code, so a browser sees why: 4400 (a token in the query string), 4401 (no valid token), 4403 (an Origin
the application does not allow, or speech not granted), 4409 (the session already has a socket), 4429 (too many
requests with this token, or a cap reached), 4500 (the engine is not available). Then:

1. the client sends `{"type": "start", "sampleRate": 16000, "language"?: "it-IT", "hint"?: "Evokai, Pipecat",
   "vad"?: {"confidence", "startSecs", "stopSecs", "minVolume"} | false}` (8000 or 16000 Hz; voice activity detection
   is Silero's, with these parameters, unless `vad` is `false`; a `hint` has at most 500 characters);
2. the server answers `{"type": "ready"}`;
3. the client sends 16-bit mono PCM as binary messages, and `{"type": "commit"}` whenever it decides an utterance
   ended; the server sends `{"type": "speech_start", "ms"}` and `{"type": "speech_end", "ms"}` as the voice activity
   detection hears them, `{"type": "interim", "text"}` and `{"type": "final", "text"}`, and `{"type": "warning",
   "code", "message"}` for a commit it ignored;
4. the client sends `{"type": "stop"}`; the server ends the open utterance, sends what is left,
   `{"type": "end", "durationMs"}`, and closes.

A malformed `start` (another sample rate, a `hint` too long) ends with `{"type": "error", "code": "start"}` and close
code 4400.

A stream with more audio than `EVOKAI_TRANSCRIBE_STREAM_MAX_SECONDS` (300), or open 10 seconds longer, ends with
`{"type": "error", "code": "too_long"}` and close code 4408; one with no audio for
`EVOKAI_TRANSCRIBE_STREAM_IDLE_SECONDS` (30) with code `idle` (text messages do not count); at the session token's
expiry with `expired` and 4401. The close reason is the code.
A synthesis that takes longer than `EVOKAI_SPEECH_TIMEOUT_SECONDS` (60) ends with an `error` event (`tts_failed`).
A recording is decoded only up to its maximum length. Engines that transcribe whole utterances (`elevenlabs`, `openai`) stream through their realtime
variants.

### Speech start and end

With voice activity detection on (the default), the socket tells the client where the person started and stopped
speaking: `speech_start` and `speech_end`, with `ms`, the position in the audio received so far. The detector confirms
speech `startSecs` after it began and silence `stopSecs` after it ended; `ms` is where it began and ended, not when it
was confirmed. A page uses them for a listening indicator, to stop the assistant's voice as soon as the person speaks
over it (barge-in) rather than when the first words are transcribed, or to time its own turn taking. `speech_end`
comes before the `final` of that utterance.

### Utterances decided by the client

An engine transcribes an utterance when it ends: a segmented engine (`whisper`, and `openai` or `elevenlabs` one
utterance at a time) sends what it buffered, a realtime engine with commits (`openai-realtime`,
`elevenlabs-realtime`) commits what it streamed. By default the voice activity detection decides where an utterance
ends. A client that knows better decides itself:

- **Push-to-talk:** `{"vad": false}` in `start`, audio while the button is held, `commit` when it is released.
- **A kiosk or a device with its own voice detection:** `{"vad": false}`, and a `commit` where its detector ends a
  phrase; no second detector runs on the server.
- **A noisy room**, where silence never comes and the detector would keep the utterance open: keep detection for
  `speech_start`, and `commit` on a timer or a gesture.

`commit` ends the current utterance at once, with detection on or off, and the server sends that utterance's `final`
when the engine heard something. With detection on and the person still speaking, the next utterance starts right
after the commit, so no words are lost; the detector's own end of speech then ends that one. Without detection an
utterance starts with the first audio after the last one ended. A commit with less new audio than the engine needs
(100 ms with any engine; 300 ms with `elevenlabs-realtime`, `SttCaps.commit_ms`) is not passed to the engine: the
server sends `{"type": "warning", "code": "commit_too_short"}` and the socket goes on. A commit that the engine
answers with nothing (silence, or a [made-up phrase](#made-up-phrases)) gets no `final`: a client that waits for one
gives up after a while. `stop` ends the open utterance like a commit (quietly when it is too short) and, with a realtime
engine, waits up to 5 seconds for its `final` before it closes.

### Vocabulary hints

`hint` (on `start`, or as a query parameter of `POST /v1/session/transcribe`) lists names and terms the person is
likely to say and the engine would otherwise misspell: product names, people and places, the jargon of a trade. Write
them separated by commas, at most 500 characters. Engines that take a vocabulary use it: `openai` and
`openai-realtime` as their prompt (models that accept one), `whisper` as its initial prompt, `elevenlabs` and
`elevenlabs-realtime` as key terms (split at commas, semicolons and new lines; at most 50 terms, each under 50
characters). The others ignore it. A factory of your own (`module:factory`) receives `hint` only when the client sent
one.

### Made-up phrases

Recognisers trained on subtitled video, the Whisper family above all, answer silence, a breath or background noise with
the credits and sign-offs that closed their training clips: «Thanks for watching», «Subtitles by the Amara.org
community», «Sottotitoli creati dalla comunità Amara.org», «Untertitel im Auftrag des ZDF». Evokai drops a transcript
that is one of them, on the socket (`final` and `interim`) and from `POST /v1/session/transcribe`. Each language pack
lists the phrases known in its language (`speech.hallucinations` in `evokai/locales/<code>.json`), and the English ones
apply in every language; a transcript that is only a sound annotation (`[Music]`, `(applause)`) or punctuation is
dropped too. A phrase is compared with the whole transcript, case and punctuation aside (`*` in a pattern stands for any
text), so «Thanks for watching the kids tonight» is kept. Every drop is logged (`stt_phrase_dropped`, with the engine
and the count, never the text) and counted in the `evokai.stt.dropped_phrases` metric when OpenTelemetry is on.

The filter is on by default. An application that wants every transcript as the engine gave it sets
`stt_filter: false` (`PATCH /v1/admin/application`, or in its configuration file).

## Clients

- **TypeScript:** `session.speak({turnId, index})` resolves with the audio and its timings; `session.speechEvents(...)`
  streams them; `TimedSpeechPlayer` plays PCM with Web Audio and emits `word`, `mark` and `viseme` as they are heard;
  `ServerTts` and `ServerStt` are a speech engine and a recogniser for the browser voice engine, which picks them when
  the token chose server engines (`session.speech`); `ServerStt` takes `vad: false` and a `hint`, emits `speechstart`
  and `speechend` (with `ms`) and `warning`, and `commit()` ends the current utterance; `session.transcribe(blob,
  {hint})` transcribes a recording.
- **`<evokai-chat>`:** voice mode uses server speech and recognition when the token chose them; browser speech stays
  the default.
- **Python:** `session.speak(text=...)` or `session.speak(turn_id=..., index=...)` returns a `Speech` (audio, words,
  marks, visemes); `session.transcribe(audio, content_type="audio/wav", hint="...")` returns the text.

## Tests

Tests never call a paid engine: the `fake` engines cover the routes, and recorded HTTP cassettes cover the real
ones once recorded with `scripts/record-cassettes.sh speech` (it needs `ELEVENLABS_API_KEY` and `OPENAI_API_KEY`, makes
a few calls with tiny texts and replays them offline). Until then those tests are skipped and say so. The streaming
socket's commits (one transcribed, one too short, the last one at `stop`) are tested through the real realtime
adapters of `openai-realtime` and `elevenlabs-realtime`, with their connection answered in the test.
