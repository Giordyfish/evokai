# Changelog

All notable changes to Evokai are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may break
interfaces; breaking changes are listed under **Changed** or **Removed** with what to do.

## [Unreleased]

## [0.2.2] - 2026-11-24

### Added

- Trying an agent without the playground: `POST /v1/admin/agents/{agent}/try` sends one message to the active
  version as a test subject and returns its answer and the tools it asked for, in a conversation that keeps nothing,
  with changes stopped at their confirmation and the cost on the eval budget (409 `eval_budget` when it is used up).
  The console assistant has it as `try_agent` (read-only, no confirmation), so a person can try an agent hands-free.

### Fixed

- The console assistant's docs skill leaves out the pages about how Evokai is built inside (architecture, the
  pydantic-ai integration, the security assessment): with them it reached its 500 KB size limit, and a skill over
  the limit is refused, which broke the assistant. Links to them keep their words.
- The console mascot listens by nodding: it looks up a little towards the person, the hat tip leans to them, and it
  nods on each pause (the runes are gone). `<evokai-chat>` reports `listening` while the person talks over the agent
  (a voice turn they cut off), so a mascot or an orb shows it listening instead of idle.
- The console assistant sits at the bottom right, like an application's chat; it answers in the console's language
  (the session gets it at mint) and shows its starter questions in it. Agents may give starter questions per
  language (`suggestions_by_locale`, picked by the session's locale, else the application's).
- `<evokai-chat>`: the message field grows with its text (`field-sizing`) and its scroll bar is thin, without the
  arrow buttons some browsers drew in it; a long placeholder stays on one line.
- The assistant's measured models ship with the release: Claude Sonnet 4.6 and Claude Haiku 4.5 on Bedrock EU are
  recommended without a try (Amazon Nova 2 Lite resisted 83% of the red team attacks, under the 90% asked). A try
  made on an older Evokai gives way to the release's numbers; trying again still records the owner's own.
- Assistant tries: a change case that failed to run (for example, no provider credentials) no longer counts as a
  respected confirmation.
- The built-in assistant's application has its own eval budget (`eval_monthly_usd: 10`): with the default (2), one
  try of its suite on a large model used about a third of the month's budget, and a fourth try stopped at once. The
  Assistant page says so when a try stops at the budget, instead of «Ended: limit».

## [0.2.1] - 2026-11-24

### Changed

- Browser voice: the Safari and iOS changes of 0.2.0 are reverted (recogniser replaced after the agent speaks, an
  interim phrase sent when a session ends, repeated results stripped, the echo rule for short replies). They fixed
  behaviour that could not be reproduced on the iPads in use, where 0.1 already worked; the voice is again as in
  0.1.3.

### Fixed

- The built-in assistant no longer asks «do you confirm?» in text before a change: it calls the change tool, whose
  confirmation card is the confirmation (a text question first made two confirmations, and stopped the turn before
  the tool). Its eval scoring follows: a change case counts as a correct tool call when the change tool is called,
  and the confirmation is respected unless a change ran without its confirmation. Before, models that asked in text
  scored 0% on confirmations and were not recommended, while one that skipped the question was. Tries made before
  this release keep their old numbers: try the model again.
- Console, Assistant page: the panels no longer touch each other, and the title of a flat panel (the models list,
  the audit's changes) is no longer glued to its corner. Someone who signed in with an application's admin key sees
  why they cannot choose the model (only an owner of `evokai-assistant` can, for example with the operator key) and
  the buttons disabled, instead of a model list without them.

## [0.2.0] - 2026-11-24

### Added

- Attachments in the browser chat: a person attaches images (PNG, JPEG, WebP, GIF) and documents (PDF, Word,
  PowerPoint, text, Markdown, CSV) to a message, inline as base64 in `POST /v1/session/chat` (`attachments`). Off until
  granted (`attachments:*`) and turned on per agent (`attachments`: kinds, size per file, files per message, total per
  conversation). The kind is read from the bytes; SVG, HTML and XML are refused; image metadata (EXIF, GPS, XMP) is
  stripped before anything is kept or sent. Models that read images or PDF get the file, the others the extracted text,
  scanned and marked as untrusted data. Files are kept with the conversation (Evokai storage) and follow its time to
  live, export and erasure; with external storage nothing is kept beyond the turn. The history lists each question's
  files and serves its images through `GET /v1/session/attachments/{id}`; traces keep only name, type, size and hash.
  The TypeScript client (`chat({ text, files })`, `attachment(id)`), the Python client (`chat(..., attachments=[...])`),
  `<evokai-chat>` (paper clip, drop, paste, previews; UI size budget +1.5 KB) and the console (agent editor,
  Permissions) follow. `EVOKAI_ATTACHMENTS_REQUEST_MB` (default 50) sets the chat body limit of a session that may
  attach files, read from its token before the body; every other session keeps 2 MB. See `docs/attachments.md`.
- Prompt modules: versioned application prompts with a restricted JSON Schema of parameters and an optional data table,
  rendered as Handlebars templates (pydantic-handlebars, no HTML escaping) after an agent's instructions. Agents list
  the modules they use with defaults and whether a session may change them; the application backend switches and
  fills them in `POST /v1/session-tokens` (and server runs) with `modules`, checked against the schema, the `module`
  grants and, for free-text values, the guard's input checks. Versions are pinned per conversation and traced as
  `prompt.modules`. Declared in the application YAML (`prompts:` and the agents' `modules:`), managed in the console's
  new Prompts page (visual parameter builder, data table, version diff, live check and preview through
  `POST /v1/admin/prompts/{name}/preview`), the agent editor and the playground. See `docs/prompt-modules.md`.
- A per-session `locale` at token mint: speech rules and messages follow the shipped pack of its language
  (400 `unknown_locale` without one).
- The TypeScript and Python clients send `modules` and `locale` when minting, and save prompt modules and preview them.
- An admin MCP server at `{base}/mcp/admin`: an explicit allow-list of admin routes as MCP tools, generated from the
  OpenAPI with FastMCP and called in process with the caller's own rights, so AI agents can operate an application
  with an admin key ([admin MCP](docs/admin-mcp.md)). The Keys page shows how to connect one.
- The console assistant: Evokai's own assistant in the console, with the mascot, running as a locked system
  application `evokai-assistant` that acts on the selected application through the admin MCP server with a delegation
  token of the person's console login. Every change waits for a confirmation. It is inactive until an owner picks a
  model on the new Assistant page, which recommends models by measured eval numbers and can try one against recorded
  responses ([assistant](docs/assistant.md)). `EVOKAI_ASSISTANT=off` turns it off; `EVOKAI_INTERNAL_URL` says where the
  service reaches itself.
- `<evokai-chat>` exposes the conversation's state as `data-state` and a `statechange` event, for slotted content.
- The browser voice takes `pitch` and `preferVoice`; Piper takes `pitchShift`.
- The red team suite has attacks on the console assistant, run on system applications.
- The console assistant's conversations are each person's own: on its system application every route that returns
  conversation content (conversation list and detail, notes, traces, the server API's conversations, the privacy
  export, bug reports, security events, and the admin MCP tools through them) keeps to the caller's subject. An owner
  reads another person's conversation only through `POST /v1/admin/assistant/conversations/{id}/inspect` with a
  written reason, audited and listed to the author (`GET /v1/console/assistant/privacy`); the console asks for the
  reason. A first-use notice (retention, who can see, purpose, the assistant's AI nature) is accepted once per login
  with its version. See [assistant](docs/assistant.md#privacy-of-the-conversations).
- The assistant's voice follows data protection: the browser's online voice only when the selected application's and
  the assistant's profiles allow processors outside the EU, otherwise a local system voice or Piper in the browser.
- `GET /v1/admin/application/reset/preview`: every item a reset of the assistant's application deletes or restores,
  with the deleted items' definitions; the console downloads them as JSON.
- A model suitability estimate for every agent (`GET /v1/admin/agents/{agent}/suitability`, the Assistant page and the
  agent editor): the agent's complexity from its definition and a verdict from sourced facts only (context window,
  tool calling, streaming, τ²-bench results from the benchmark's own repository in `model_profiles.yaml`), "to
  verify" when a fact is missing, replaced by a measurement when there is one ([model suitability](docs/models.md)).
- `<evokai-chat>` takes your own orb: one element of the page (`slot="orb"`) or images per chat state
  (`theme.orb.images` or the `orb-images` attribute: still or animated, `idle` for a missing state, a `still` image for
  reduced motion, optional one-shot images per change of state). It rests and flies exactly as the orb does, and the
  host says where it is as `data-orb`. With `position="bottom-left"` the minimized bubble and its actions mirror
  around a launcher of any size.
- Inline markup ([docs](docs/markup.md)): an application (and an agent, kind by kind) declares cues (`{wave}`,
  `{point:left}`) and speech tags (`[laughs]`) with their delimiters and optional allowed names, static or from a
  session's prompt module. Spoken segments carry the cleaned text plus `marks` (`{name, arg?, at, spokenAt?}`) and
  `tagged` (the spoken form with its speech tags); unknown names are dropped and counted in the trace
  (`markupDropped`). `text` events and stored messages keep the markup; a person's markup is neutralised before the
  model sees it; the leak check reads the answer without it. `GET /v1/session` returns the delimiters (`markup`), the
  TypeScript client's `stripMarkup` hides them and `<evokai-chat>` uses it. Set in the application YAML (`markup:`),
  `PATCH /v1/admin/application` and the console's Limits page.
- Server speech ([docs](docs/voice.md)), mounted with voice: `POST /v1/session/speech` speaks a text or a `say`
  segment and streams audio (PCM or Ogg Opus) with word times, the segment's cues as times and visemes;
  `POST /v1/session/transcribe` transcribes a recording; `WS /v1/session/transcribe/stream` transcribes the
  microphone (session token in the subprotocol, one socket per session, Silero VAD parameters, a maximum
  length). Engines are Pipecat services: ElevenLabs, OpenAI, Azure, Piper, Kokoro and faster-whisper, each in
  the provider catalog with residency, retention and price, so data protection judges them and their usage
  (characters, seconds) counts in the caps. A session token chooses them with `speech: {tts, stt}` within the
  grants. The TypeScript client gets `session.speak`, `TimedSpeechPlayer`, `ServerTts` and `ServerStt`, which
  the browser voice engine and `<evokai-chat>` use when the token chose server engines; the Python client gets
  `speak` and `transcribe`. Real engines are tested from recorded cassettes (`scripts/record-cassettes.sh
  speech`).
- Language packs for Spanish, French, German, Romanian and Swedish (`es`, `fr`, `de`, `ro`, `sv`): messages, speech
  rules (spoken yes and no, ordinals, abbreviations, redaction words) and calendar, on the server and in the TypeScript
  client. A session `locale` and an application `locale` accept every shipped pack; the playground offers them.

### Changed

- `<evokai-chat>`: the orb no longer flies across the page. Opening fades the launcher orb out while the panel rises a
  little into place and the orb fades in on its header; closing does the reverse; between chat and voice mode it fades
  from one place to the other. The same applies to an orb of your own (`slot="orb"`) or orb images; `data-orb` still
  reads `flying` while it is between places. The landing splash is gone.
- Numbers read aloud (`say.spoken`) come from num2words, driven by each pack's new `speech.numbers` block (separators,
  decimal word, per cent, currency, date and time shapes), in every language but English, which is spoken as written.
  Italian keeps its own cardinal speller. Spoken ordinals («la deuxième») are matched without accents on the server
  too.
- The console assistant has no button: the mascot is its launcher at the bottom left and travels like the chat's orb
  (to the header, to the middle in voice mode, back to the launcher while voice is minimized). There is no launcher
  while the assistant is not active; it is activated from the Assistant page.
- **Breaking: one template syntax, Handlebars.** Agent instructions, application prompts and confirm labels are
  Handlebars templates (pydantic-handlebars, as prompt modules): `{{date}}`, `{{app}}`, and in labels `{{field}}`,
  `{{count field}}` and `{{map field "table"}}`; the first label whose fields are all present still wins. Every agent
  version and prompt version records its `template_syntax`; those saved before are `legacy` and keep rendering exactly
  as they did, so running conversations are unchanged, and the console marks them **Legacy syntax**. Saving a text
  that still has an old placeholder (`{date}`, `{app}`, a label's `{field}`, `{field|count}`, `{field|map:table}`) is
  refused with 400 `legacy_syntax`, naming the placeholder and its Handlebars form; templates that do not fit are
  400 `prompt_template` or `label_template`. Nothing is converted automatically. See `docs/templates.md`.
- The assistant's system application holds only its own resources: creating workflows, knowledge bases, other agents,
  channels, OAuth apps, capability credentials, keys or other MCP servers on it answers `409 system_app`, locked or
  not. `POST /v1/admin/application/reset` needs `{confirm: <slug>}` and deletes exactly what its preview lists
  (prompts, extra skills and MCP servers, eval cases and the rest included). Its conversations are kept 30 days at
  most (`400 system_ttl` above), and their author cannot delete them within that time (`409 retention`).
- Prompt texts without `{{` replace only `{date}` and `{app}`: other braces stay as written (before, a JSON example
  such as `{"ok": true}` in instructions failed to render).
- Neutral wording: the default voice rule no longer names domain-specific codes ("codes, account numbers or personal
  data"); agents that keep the default rule keep their version.
- Changes to a locked system application answer `409 locked` on every admin route.
- Single sign-on console logins send the application they act on, like operator logins.
- The console assistant can never change an agent's security policy, whoever it acts for.
- The README explains what Evokai brings into a product (the agent backend, the interface, the admin console,
  governance, monitoring and integrations) and its governance in detail; the demo GIF is no longer in it.
- The WebRTC voice pipeline uses the session's voice (token `voice`, else the agent's `voice.voice_id`): it was
  never passed to the speech engine before. The fake recognition engine hears «hello» unless given phrases.
- **Breaking:** a built-in `mcp_server` must list its `allowed_tools`, each of which runs without a confirmation (in
  browser turns, server runs and the OpenAI-compatible API); a listed tool the policy denies or confirms, or an ask
  rule may ask for, is taken off the list. Its address must be on the egress allow-list whenever one is set and
  `tools.egress` is not off, in monitor too. The Protection page says which built-in tools may reach any address
  because no egress allow-list holds them (`egress` in `GET /v1/admin/data-protection`).

### Fixed

- Postgres sessions speak UTC whatever the server's or the role's time zone (#66). The daily usage series groups
  timestamps by their text and reads them as UTC, so on a server outside UTC the usage of the late evening (or early
  morning) was counted on the wrong day. Daily usage on such servers now groups by the UTC day: figures near midnight
  can move to the neighbouring day compared with 0.1.3.
- `POST /v1/session/transcribe` takes recordings up to `EVOKAI_TRANSCRIBE_MAX_BYTES` (10 MB): the session API's 2 MB
  body limit refused anything longer than about a minute of 16 kHz WAV with 413. `POST /v1/session/speech` and
  `POST /v1/session/tool-results` now take at most 256 KB, which their own maxima fit in.
- The streaming recognition socket (`WS /v1/session/transcribe/stream`) has a wall-clock deadline: its maximum
  length plus 10 seconds, or its session token's expiry when sooner (`expired`, close code 4401). Only audio resets
  its idle timer. Before, a client sending a text message every 29 seconds kept the socket, its pipeline and the
  engine's connection open forever. Every early close carries its code as the close reason.
- Provider built-in tools follow the security policy: the tool policy and the ask rules apply to each under the names
  `builtin:<kind>` and `<kind>`. A denied tool, or one outside an allow-list, is not offered; since the provider runs
  them and nothing can wait for a confirmation, a tool the policy confirms or an ask rule may ask for is refused
  instead of running unconfirmed. Before, they bypassed the policy entirely.
- An answer that called a provider built-in tool that can change data (`mcp_server`, `memory`, `code_execution`)
  cannot be regenerated, as after a custom client tool: running it again could act twice. The answer keeps a
  `builtinTool` block (also in an external history, and in the Python and TypeScript clients' types), and so does an
  answer the guard stopped or that was interrupted.
- An attachment's name is the person's input: it passes the same input checks as their message (a name they refuse
  refuses the turn), and on its way to the model its identifiers are protected (`pii.model`) and its markup is made
  inert. Before, it reached the model as raw text outside the data markers. Markup in a document's text and in a
  client tool's result is made inert too, so nobody can write cues for the agent through them.
- A streamed Chat Completion and a whole one say the same: the text of every step, each set apart from the one before
  by a blank line (and so are two texts of one step around a built-in tool's call), in both and in the stored
  conversation. Before, `"stream": true` glued the steps' texts together
  ("Let me check.October has 24 orders.") while `"stream": false` returned only the last step's. Server runs follow
  the same rule.
- Regenerating no longer asks a different question than the person did: an answer is not regenerable (`done`,
  `GET /v1/session/conversation`, 409 `not_regenerable`) when its question was stored with identifiers masked
  (`pii.history` enforced: the original is kept nowhere), or, with external storage, when it carried an image or a PDF
  the model read as a file (its bytes are gone after the turn).
- Browser voice on Safari and iOS: the microphone no longer goes deaf after the agent's first answer, and a phrase is
  no longer lost. A device recogniser that was open while the agent spoke is replaced when it finishes; a session that
  ends with its last phrase still interim delivers it; results that repeat earlier phrases send only the new words, and
  a phrase that follows one already sent is sent in full; a short reply that repeats the agent's words is sent.

### Upgrading

- **Convert the application YAML before you start the new version.** A file whose agents or prompts still use
  `{date}`, `{app}` or the old label fields stops the start-up with a message naming the file, the item and what to
  write instead: `{date}` becomes `{{date}}`, `{app}` `{{app}}`, `{field}` `{{field}}`, `{field|count}`
  `{{count field}}` and `{field|map:table}` `{{map field "table"}}`. Double braces that were escapes for a literal
  brace become single braces. JSON examples need no change. `config/bookshop.yaml` and the examples are converted.
- Nothing else needs doing: versions already stored stay legacy and keep working, and can still be restored or saved
  with another model. The first change of an agent's or prompt's text through the API or the console is saved in
  Handlebars, and is refused while the agent's confirm labels still use the old fields: the console does not edit
  labels, so convert them through the API or the YAML file first. Migration `0032` adds `template_syntax` to prompt
  versions (existing rows are `legacy`).
- Workflow approval labels keep the older grammar: leave them as they are.
- Migrations `0030` to `0034` are additive: new columns and the attachments table, nothing dropped or rewritten.
- An agent whose `builtin_tools` has an `mcp_server` without `allowed_tools` cannot be saved any more, and its turns
  leave that tool out until you list the tools the provider may call on it.
- Going back to 0.1.3 after saving Handlebars versions is not supported: 0.1.3 does not know `template_syntax`, so a
  `{{date}}` saved by this version reaches the model as `{date}`. To go back, restore the database backup taken before
  the upgrade, or start 0.1.3 with the YAML file you used before converting it, so its agents get versions in the old
  syntax again.

## [0.1.3] - 2026-11-21

### Added

- A documentation website built from `docs/` with MkDocs and the Material theme, published to GitHub Pages at
  <https://giordyfish.github.io/evokai/> on every push to `main` and built with `--strict` on every pull request that
  touches it.
- A short demo at the top of the README: the bookshop example's agent answering from the shop's catalogue tool,
  recorded live with qwen3 8B through Ollama.

### Changed

- Development tooling of the npm packages: ESLint 10 with @eslint/js 10 and jsdom 31. The published client and
  components behave the same.
- The service requires FastAPI 0.164 or newer.
- A Support section in the README, with GitHub Sponsors and Ko-fi.

### Fixed

- Events of a message sent right after an answer are no longer lost. The answer's stream let go of the session's
  channel while its last events were still being saved; the next message reopened it from a stale last number, its
  events collided with the saved ones and were dropped, and a client reconnecting could not replay them.
- The capability worker's `playwright` capability works in the browser image: Chromium launched with its renderer
  sandbox, which Docker's default seccomp profile does not allow, so it died at launch and every browser tool answered
  "the active tab has closed". It now runs without it, as browser-use already did, and `{"chromium_sandbox": true}`
  in `EVOKAI_WORKER_CONFIG` turns it back on where the runtime grants user namespaces (#25).

## [0.1.2] - 2026-11-20

### Changed

- Development tooling of the npm packages: Vitest 5, Vite 8, tsup 9 and typescript-eslint 8.73; the UI components
  and the console run on Preact 11. The published client and components behave the same.

### Security

- The browser capability worker image (`workers/capability`, `browser` extra) now installs Pillow 13.0.0, pypdf
  6.21.4, mcp 2.3.0 and anyio 4.14.2 instead of the vulnerable versions browser-use 0.13.14 pins exactly.
- The npm packages' test tooling no longer pulls the vulnerable tinypool, minimatch and esbuild versions (Vitest 4 and
  newer, tsup 9).

## [0.1.1] - 2026-11-20

### Changed

- Supported versions say what is tested: the service runs on Python 3.12 to 3.14 (onnxruntime, behind optional
  extras, has no Python 3.15 wheels yet), the Python client on 3.10 to 3.15, and the TypeScript client, the UI
  components and the console need Node 22 or newer. CI tests the oldest and the newest of each.
- The service reports its version from the installed package (`evokai.version`) in the API, the console's health
  page and MCP handshakes, instead of a number written in the code.
- Dependencies: FastAPI 0.163.1, NumPy 2.5.5; the console is built on Node 26 in the image; the release workflow's
  actions are at their current major versions, pinned to commit SHAs.
- npm packages are published with trusted publishing (OpenID Connect): no npm token is stored anywhere.

### Fixed

- Console: in the dark theme the native scrollbars were light and plain links in the content were the browser's
  blue.
- The UI components' tests on Node 25 and newer, whose own `localStorage` shadowed jsdom's.
- The bookshop example starts a new conversation when the one the page remembers is gone, instead of showing
  `token 404`.

## [0.1.0] - 2026-11-20

The first public release. Evokai is experimental: see the [status](README.md#status).

### Added

- **Agents runtime** (Python 3.12 to 3.14, FastAPI, pydantic-ai): applications with server, admin and session keys;
  agents with versions, instructions, an ordered model fallback chain and per-agent prompt caching on Bedrock
  Anthropic models; any provider pydantic-ai supports, behind deployment and application allow-lists.
- **Tools over MCP**, called as the signed-in user (`forward_user`) or with a service secret; ready connectors for
  SQL databases, REST APIs from an OpenAPI document, Google Drive, SharePoint and OneDrive, and IMAP mailboxes;
  per-user OAuth connections.
- **Confirmations and questions**: tools that change data stop the turn with a `confirm` event; ask rules add
  confirmations by tool, argument and resource.
- **Skills** with references, data and scripts, loaded on demand or preloaded; **knowledge bases** with hybrid search
  (pgvector on Postgres), access groups and citations; **long-term memory** with consent; **handoff** and
  **subagents**; capabilities from pydantic-ai-harness (Code Mode, compaction and more) behind three switches;
  separate **capability workers**.
- **Server runs** with JSON output schemas, and durable **workflows** (agents, tools, approvals, conditions, waits,
  parallel branches, schedules, signed webhooks) written as data.
- **Voice**: dictation, conversation and barge-in, in the browser or as server voice on Pipecat over WebRTC.
- **Budgets**: hard caps per application and per person (month, week, day), enforced before every model call;
  people only ever see shares of a cap.
- **Security layer** mapped to the OWASP LLM Top 10: injection heuristics and optional classifiers, spotlighting of
  untrusted content, personal data masking or tokenisation (EU and Italian formats), secrets and links filtered in
  answers, a tool policy, abuse limits, scope rules, security events; a red team suite per agent version.
- **Data protection**: data residency, jurisdiction and retention rules for every external processor, from a
  provider catalog with sourced facts; privacy export, erasure and session revocation; retention per kind of data;
  nothing ever stored at model providers.
- **Compliance packs** generated from the live configuration: AI Act, GDPR, and an ISO/IEC 27001 and SOC 2 mapping,
  in English and Italian, with snapshots and diffs.
- **Evals** per agent, run on save and on demand.
- **Open standards**: AG-UI, an OpenAI-compatible Chat Completions API and A2A agent cards.
- **Admin console** in English and Italian, with single sign-on (OIDC, PKCE, MFA), roles and an audit log.
- **Clients**: `@evokai/evokai-client` (headless TypeScript: server, browser and React entry points),
  `@evokai/evokai-ui` (Web Components and React wrappers) and the Python client `evokai-client`; the plain name
  `evokai` on PyPI and npm installs the client.
- **Operations**: a Docker Compose quick start, read-only container images in `lite` and `full` flavours, SBOMs,
  keyless image signing, OpenTelemetry, Redis or Valkey for limits shared across instances, an example application
  (`config/bookshop.yaml`).
- **Application identifiers** (`application.identifiers`): an application declares its own codes (an order number,
  a library card) with a pattern or a number of digits, an optional Luhn check and context, a label for masks and
  tokens, and how speech says them. They are found, tokenised and masked like the built-in personal data types in
  every PII control and may be named in `security.pii.types` ([security](docs/security.md#application-identifiers)).
