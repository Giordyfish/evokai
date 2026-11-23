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

The WebRTC voice sessions ([architecture](architecture.md#voice)) use the same engines.

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
POST /v1/session/transcribe?language=it-IT
Content-Type: audio/webm

<the recording>
```

WAV, WebM, Ogg, MP3 or M4A, up to `EVOKAI_TRANSCRIBE_MAX_BYTES` (10 MB, else 413 `too_large` or `audio_too_large`;
this route's body limit is that setting, not the session API's 2 MB) and
`EVOKAI_TRANSCRIBE_MAX_SECONDS` (120, else 400 `audio_too_long`); anything else is 400 `audio_invalid`. The answer is
`{text, durationMs, engine}`. `language` defaults to the session's.

## Streaming recognition

The socket authenticates with the session token in its subprotocols, never in the URL: the client offers `evokai.v1`
and `evokai.token.<session token>`, and the server accepts `evokai.v1`. A refused socket is accepted and closed at
once with a code, so a browser sees why: 4400 (a token in the query string), 4401 (no valid token), 4403 (an Origin
the application does not allow, or speech not granted), 4409 (the session already has a socket), 4429 (too many
requests with this token, or a cap reached), 4500 (the engine is not available). Then:

1. the client sends `{"type": "start", "sampleRate": 16000, "language"?: "it-IT", "vad"?: {"confidence", "startSecs",
   "stopSecs", "minVolume"}}` (8000 or 16000 Hz; voice activity detection is Silero's, with these parameters);
2. the server answers `{"type": "ready"}`;
3. the client sends 16-bit mono PCM as binary messages; the server sends `{"type": "interim", "text"}` and
   `{"type": "final", "text"}`;
4. the client sends `{"type": "stop"}`; the server sends what is left, `{"type": "end", "durationMs"}`, and closes.

A stream longer than `EVOKAI_TRANSCRIBE_STREAM_MAX_SECONDS` (300) ends with `{"type": "error", "code": "too_long"}` and
close code 4408, and so does one that receives nothing for `EVOKAI_TRANSCRIBE_STREAM_IDLE_SECONDS` (30, code `idle`).
A synthesis that takes longer than `EVOKAI_SPEECH_TIMEOUT_SECONDS` (60) ends with an `error` event (`tts_failed`).
A recording is decoded only up to its maximum length. Engines that transcribe whole utterances (`elevenlabs`, `openai`) stream through their realtime
variants.

## Clients

- **TypeScript:** `session.speak({turnId, index})` resolves with the audio and its timings; `session.speechEvents(...)`
  streams them; `TimedSpeechPlayer` plays PCM with Web Audio and emits `word`, `mark` and `viseme` as they are heard;
  `ServerTts` and `ServerStt` are a speech engine and a recogniser for the browser voice engine, which picks them when
  the token chose server engines (`session.speech`); `session.transcribe(blob)` transcribes a recording.
- **`<evokai-chat>`:** voice mode uses server speech and recognition when the token chose them; browser speech stays
  the default.
- **Python:** `session.speak(text=...)` or `session.speak(turn_id=..., index=...)` returns a `Speech` (audio, words,
  marks, visemes); `session.transcribe(audio, content_type="audio/wav")` returns the text.

## Tests

Tests never call a paid engine: the `fake` engines cover the routes, and recorded HTTP cassettes cover the real
ones once recorded with `scripts/record-cassettes.sh speech` (it needs `ELEVENLABS_API_KEY` and `OPENAI_API_KEY`, makes
a few calls with tiny texts and replays them offline). Until then those tests are skipped and say so.
