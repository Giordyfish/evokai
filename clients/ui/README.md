# @evokai/evokai-ui

A ready interface for Evokai agents that drops into any page: **`<evokai-chat>`** (a launcher orb and a chat panel,
with voice mode inside it) and **`<evokai-voice>`** (a full-screen voice stage, for a kiosk or a voice button of your
own), as Web Components with **React wrappers**. Transport, events,
voice and usage come from the headless client [`@evokai/evokai-client`](../typescript/README.md); this package draws.

What the panel does: answers in Markdown (tables, code, links that stay in your app), tool activity with your labels,
charts and schemas (the service's `chart` and `diagram` payloads), questions with options (keys 1 to 5), confirmations
before any change, starter questions, the conversation list, thumbs up and down, a usage ring that shows **shares of
the caps only** (there is no amount anywhere), dictation into the message box and voice mode: on-device recognition,
local speech, talking over the agent (it holds, resumes from the word it reached, or stops and listens), and strict
turns («Interrupt by speaking» off). With long-term memory on, it also asks the person once whether the agents may
remember things about them and shows what is remembered, to change or delete (see [Memory](#memory)).

## One orb, four views

There is only ever one orb on screen. It rests in the launcher (panel closed), in the header (chat) or in the middle of
the panel (voice mode), and flies between them (transform only, a slight arc, a small splash where it lands). When
something opens the orb moves first and the panel's box opens around it once it has landed; when something closes the
box closes first and the orb travels after. Turning voice on or off inside the open panel flies the orb between the
header and the middle while the chat and voice layers crossfade.

- **closed**: the launcher orb, bottom right.
- **chat**: the panel; the launcher is hidden while it is open, so on a desktop the panel reaches the bottom margin.
  On a phone the panel is full screen.
- **voice**: voice mode inside the panel, the page behind still visible and usable: the orb (the user's voice swells
  it, the agent's moves the plasma, a ring turns while it thinks), the status, the subtitles (said and still to say),
  the latest charts under them, keyboard, mute and end, and «Interrupt by speaking». The header shows the phase and a
  **minimize** button next to close.
- **minimized**: the panel closes and the conversation goes on in the launcher orb, which follows the voice. A bubble
  beside it shows the status, the agent's line or what the user is saying, and a chip for the latest chart («Open»
  brings the panel back). Hovering the orb (or moving the keyboard focus to it) raises mute, restore and end above it;
  a click on the orb restores the panel. End stops voice and leaves the panel closed.

Closing the panel from voice mode ends voice; closing it any other way while voice is on (`close()`, Ctrl/⌘+J, the
React `open` prop) minimizes it, and `open()` restores it. The view is on the host as `data-view` (`closed`, `chat`,
`voice`, `minimized`). With `prefers-reduced-motion` nothing flies: every change is instant.

## Install

```bash
npm install @evokai/evokai-ui @evokai/evokai-client
```

## Use it

Your backend mints the session token with its server key ([client README](../typescript/README.md)); the page needs
an endpoint that returns the mint response for the signed-in user:

```html
<script type="module">
  import { defineElements } from '@evokai/evokai-ui'
  defineElements()
</script>

<evokai-chat
  base-url="https://agents.example.com/agent"
  token-url="/api/agent/token"
  conversations-url="/api/agent/conversations"
  title="Ada"
  app-name="Inkwell Books"
  locale="en"
  theme="evokai"
></evokai-chat>
```

`token-url` is called with `POST {"conversationId": string | null}` (same-origin cookies included) and must answer
what Evokai's `POST /v1/session-tokens` answers (`token`, `conversationId`, `expiresAt`, `usage`, `suggestions`); add
`"restarted": true` when you replaced an expired conversation. `conversations-url` (optional) answers
`{"items": [{"id", "title"}]}`, for example from `EvokaiServer.conversations.list({subject})`.

Evokai must accept the page's origin: list it in the application's `allowed_origins`. Pages on another origin than
Evokai call it directly (the service answers CORS for its session routes, for allowed origins only); a same-origin
proxy works too.

### React

```tsx
import { EvokaiChat } from '@evokai/evokai-ui/react'

<EvokaiChat
  baseUrl="/agent"
  tokenUrl="/api/agent/token"
  title="Ada"
  theme={{ light: { brand: '#0b3d91' } }}
  toolLabels={{ order_get: 'Reading the order' }}
  onNavigate={(path, e) => { e.preventDefault(); router.push(path) }}
/>
```

`ref` gives the element: `open()`, `close()`, `toggle()`, `send(text)`, `startVoice()`.

## Attributes, properties, events

| Attribute | Default | Meaning |
| --- | --- | --- |
| `base-url` | | Where Evokai is mounted, base path included |
| `token-url` | | Your token endpoint (or the `getToken` property) |
| `conversations-url` | | Your conversation list (or `listConversations`); without one the history button is hidden |
| `title`, `app-name` | `Assistant`, none | The assistant's name (`{title}` in the texts) and your application's (`{app}`) |
| `locale` | `en` | `en` or `it`: texts, speech rules, voices |
| `theme` | `evokai` | A preset (`evokai`) or, as a property, a `Theme` object |
| `unstyled` | | Drop the default look; only the tokens and a reset remain (see [Skins](#skins)) |
| `scheme` | system | `light` or `dark` |
| `position` | `bottom-right` | `bottom-left` moves the launcher and the panel |
| `voice` | on | `off` hides voice mode |
| `shortcut` | `j` | Ctrl/⌘ + this key toggles the panel; `none` |
| `storage-key` | `evokai` | Remembers the open conversation (and the «Interrupt by speaking» choice) in this browser; empty: nothing |
| `page-context` | on | `off`: no page text is sent with messages |
| `page-selector` | `main` | The element whose text is the page (the components are never part of it) |
| `open` | | The panel is open (reflects `open()`/`close()`) |

Properties: `getToken(ctx)`, `session` (an `EvokaiSession` of yours), `listConversations()`, `getPage()` (or `null`),
`theme`, `strings` (any text, see `Strings`), `toolLabels` (a map or a function), `formatValue(value, unit, locale)`
(how chart values are written: plain numbers by default, `pct` with a percent sign; units such as `eur` are your
application's business figures, never Evokai's costs), `voiceOptions` (microphone sound, rate, barge-in), `skin`
(your CSS inside the component), `icons` (replaced icons) and `fetcher`.

Events (bubbling, composed): `evokai-open`, `evokai-close` (also when voice is minimized), `evokai-event` (every session event, `detail` is the
event), `evokai-navigate` (`detail.path`, cancelable: by default a relative path is opened with `location.assign`;
call `preventDefault()` and route it yourself in a single-page application), `evokai-voice-end`.

`<evokai-voice>` takes the same configuration, or `for="<id of an evokai-chat>"` to continue that panel's
conversation. `start()` (from a click: the microphone needs a user gesture) opens the stage; `stop()` closes it.

## Citations

When an agent searches its knowledge bases, the service sends a `citation` event for each passage it found and the
answer cites them as `[1]`, `[2]`. The panel draws each marker of a known citation as a superscript number (a link to
the document when the citation has an address: the application's own link, or a signed, short-lived link to the file)
and lists the cited sources under the answer: the document's title, its page and section, and the start of the
passage. Markers without a citation stay as text. Texts: `sources`, `sourcePage` and `searching` (the label of the
search while it runs); parts: `citation`, `sources`, `source`. Both schemes use the theme's tokens (`muted`, `brand`,
`border`, `subtle`).

## Connections

When the application configured and granted a provider people can connect (Microsoft 365, Google Workspace: see the
service's `docs/connectors/connections.md`), the header menu also offers **Connections** («Collegamenti» in Italian),
next to the memory entry. The view, built like the memory view, lists each provider with its state (connected,
expired, not connected), the account, what the agent may use with it and when it last did, with **Connect** (the
provider opens in a new window; the list is read again when it closes) and **Disconnect** (after a confirmation: the
person's tokens are deleted). When a tool needs a provider the person has not connected, the answer carries a card
(«Connect Microsoft 365») whose button starts the connection the same way. Texts: `connectionsMenu`,
`connectionsTitle`, `connectionsIntro`, `connect`, `disconnect`, `connectCardTitle`, `connectCardText` and the others
starting with `connection`.

## Memory

When the application has long-term memory (`memory_mode` `ask` or `on`), the header gets a menu (the `⋯` button) with
**«What {title} remembers»** («Cosa ricordo di te» in Italian). The entry is hidden when the mode is `off` (or the
service does not report memory). The view replaces the conversation in the panel until the person goes back (the
arrow, or Escape):

- What is remembered, grouped by kind: how the person likes answers (`preference`), their work (`semantic`) and past
  conversations (`episodic`, short summaries of finished conversations). Each memory shows its text and a light line
  with its date and «edited by you» when the person rewrote it.
- **Edit** in place (Enter saves, Shift+Enter is a new line, Escape cancels). When the service refuses the text
  (instructions, secrets or personal data: `memory_refused`) the reason is said in plain words under the box.
- **Delete** one memory, or **Forget everything** after a confirmation.
- **Turn off memory**, after a confirmation that says it deletes everything; a person who said no (or turned it off)
  sees that memory is off for them and can **Turn on memory** again.

With `memory_mode: ask` and no answer yet, the panel asks once, after the first answer, in a small card above the
message box: «May I remember things about your work to help you better? You can see, change or delete them at any
time.» with «Yes» and «No thanks». The card never blocks the conversation; the answer goes to the service
(`memoryConsent`), and it is also kept in this browser (under `<storage-key>.memoryAnswered`) so the card does not come
back. A no deletes what was remembered. An application that asks in its own interface passes the answer when it
mints the token instead (`memoryConsent` on `POST /v1/session-tokens`, see the [client](../typescript/README.md)).

Texts: the `memory*` keys of `Strings` (and `menu`, `retry`). Icons: `more`, `back`, `memory`, `edit`, `trash`. Parts:
see the list under [Skins](#skins). Everything is drawn with the theme's tokens (`muted`, `border`, `subtle`, `brand`,
`danger`), so it follows skins and both schemes.

## Theming

Everything is a CSS custom property, so your stylesheet can theme the components:

```css
evokai-chat, evokai-voice {
  --evokai-brand-light: #0b3d91;  --evokai-brand-dark: #8fb3ff;   /* or --evokai-brand for both schemes */
  --evokai-accent: #ff6b35;
  --evokai-font: "Inter", system-ui, sans-serif;
  --evokai-radius: 14px;
  --evokai-orb-ink: #06173a;  --evokai-orb-main: #0b3d91;  --evokai-orb-light: #8fb3ff;  --evokai-orb-glow: #fff4ec;
}
```

Colours: `surface`, `muted`, `text`, `subtle`, `border`, `brand`, `on-brand`, `accent`, `danger`, `veil` (the voice
stage's backdrop) and `series-1` to `series-5` (charts), each as `--evokai-<name>`, `-light` or `-dark`; `aurora-1` to
`aurora-3` colour the glow behind the header. Custom properties are inherited, so they can point at your own design
tokens (`--evokai-surface: var(--background)`) and follow your light and dark themes. The same as an object:

```ts
chat.theme = {
  scheme: 'auto',
  light: { brand: '#0b3d91', onBrand: '#fff', accent: '#ff6b35' },
  dark: { brand: '#8fb3ff', onBrand: '#06173a' },
  font: 'Inter, sans-serif',
  radius: '14px',
  logo: '/logo.svg',            // shown in the header instead of the small orb
  orb: { ink: '#06173a', main: '#0b3d91', light: '#8fb3ff', glow: '#fff4ec' },
}
```

A brand theme belongs to the application (its stylesheet or its code), not to this package.

## Skins

Tokens change colours and fonts. For a different look altogether, keep every behaviour (streaming, tools, charts,
questions, confirmations, usage, history, dictation, voice) and redraw the components:

- **Parts.** Every element worth styling has a stable `part` name. From your page: `evokai-chat::part(user-bubble)`.
  Inside a skin: `[part~="user-bubble"]`. Class names are internal and may change; parts do not.
- **Skin.** `chat.skin = css` (a string or a list; React `skin`) puts your CSS inside the component's shadow root. The
  default look sits in the cascade layer `evokai`, so a skin always wins over it whatever its selectors, and can reach
  what `::part()` cannot (the Markdown inside an answer, the cards, the voice stage).
- **Unstyled.** With the `unstyled` attribute (React `unstyled`) the default look is dropped: only the tokens, a reset
  and the behaviour remain, and the skin draws everything.
- **State on the host.** `data-phase` (`idle`, `listening`, `thinking`, `speaking`, `interrupted`), `data-busy`,
  `data-voice`, `data-dictating` and `data-view` (`closed`, `chat`, `voice`, `minimized`) are kept on the element: `evokai-chat[data-phase=speaking]::part(title)`, or
  `:host([data-busy]) ...` in a skin.
- **The conversation's state, for slotted content.** `data-state` on the host says in one word what the conversation is
  doing: `idle`, `listening` (voice mode listens, or the person dictates), `thinking`, `tool` (a tool runs), `confirm`
  (a change waits for the person's answer), `speaking` or `done` (the turn ended). Each change also fires a
  `statechange` event (`detail: { state, previous }`), so a figure of your own in a slot can follow the conversation;
  `chatState` and `fromEvent` (exported) compute it from a controller and its events.
- **Slots.** Children of the element with `slot="launcher"` (the launcher's content), `slot="avatar"` (the header's
  orb or logo), `slot="empty"` (above the starter questions) or `slot="stage-orb"` (the sphere of voice mode) replace
  or add to those places (where a slot is filled, the travelling orb hides while it rests there); in React pass them as children. The element's `controller` gives your own pieces the state
  and the voice signals.
- **Icons.** `chat.icons = { up: '<svg ...>...</svg>', mic: '<path d="..."/>' }`: a whole SVG, or its content drawn in
  a 24 by 24 box with `currentColor`. Names: `history`, `plus`, `x`, `mic`, `wave`, `up`, `stop`, `thumbUp`,
  `thumbDown`, `more`, `back`, `memory`, `edit`, `trash`, `keyboard`, `minimize` and `restore` (one family: an arrow
  into the orb's corner and out of it), `chart` (the bubble's chart chip).

Parts: `root`, `dock` (the launcher with the bubble and its buttons), `launcher`, `orb` (the travelling orb),
`orb-slot` (its three resting places, with `data-rest` and, where it rests, `data-here`), `panel` (with `data-box`
and `data-layer`), `aurora`, `header`, `avatar` (a logo), `title`, `tagline`, `usage`, `usage-tip`,
`header-buttons`, `header-button` (with `menu-button`, `history-button`, `new-button`, `minimize-button`,
`close-button`), `menu`,
`menu-item` (with `memory-menu-item` or `connections-menu-item`), `history`, `history-item`,
`messages`, `starters`, `starter`, `message` (with `user-message` or `assistant-message`), `user-bubble`, `answers`,
`answer-chip`, `markdown`, `citation` (a `[n]` marker, in superscript), `sources` (the list under an answer) and
`source` (one entry, with `data-index`), `tool`, `navigation`, `error`, `chart`, `card` (with `ask-card`,
`confirm-card` or `connect-card`, whose button is `connect-button`), `confirm-reason` (why an item waits, when an
ask rule asked), `edit-message` (the button that edits the last question while it can be rewritten), `editing` (the
notice while it is being edited),
`feedback`, `thinking`, `notice`, `composer`, `field`, `input`, `composer-button` (with `dictate-button`,
`voice-button`, `send-button`, `stop-button`); voice mode (the layer inside the panel, and the full-screen stage of
`<evokai-voice>`): `stage`, `stage-orb`, `stage-status`, `stage-notice`, `subtitles`, `said`, `to-say`,
`stage-controls`, `stage-button` (with `keyboard-button`, `mute-button`, `end-button`), `talk-over`, `stage-cards`,
`stage-card`; minimized voice: `voice-bubble` (with `data-phase`), `voice-bubble-status`, `voice-bubble-text`,
`voice-bubble-chart`, `voice-bubble-open`, `voice-actions`, `voice-action` (with `mute-action`, `restore-action`,
`end-action`); memory: `memory-view`, `memory-back`, `memory-title`, `memory-group` (with `data-kind`),
`memory-item` (with `data-kind` and `data-id`), `memory-text`, `memory-meta`, `memory-edit`, `memory-editor`,
`memory-input`, `memory-save`, `memory-cancel`, `memory-delete`, `memory-error`, `memory-empty`, `memory-off`,
`memory-forget-all`, `memory-consent` (turn memory on or off, with `data-action`), `memory-confirm` (an inline
confirmation) and `memory-confirm-yes` (its action), `memory-ask` (the consent card) with `memory-ask-yes` and
`memory-ask-no`, `memory-note` (the short note after the answer, also a `notice`); connections: `connections-view`,
`connections-back`, `connections-title`, `connections-list`, `connection-item` (with `data-provider` and
`data-status`), `connection-name`, `connection-state`, `connection-meta`, `connection-families`, `connection-waiting`,
`connection-connect`, `connection-disconnect`, `connection-confirm` and `connection-confirm-yes`, `connection-error`,
`connections-empty`, `connections-error`.

```css
/* A page stylesheet: square bubbles in the brand's ink, a serif title, a glow while the agent speaks. */
evokai-chat::part(user-bubble) { border-radius: 4px; background: #101820; color: #f2efe6; }
evokai-chat::part(title) { font-family: "Fraunces", serif; }
evokai-chat[data-phase=speaking]::part(launcher) { box-shadow: 0 0 0 6px rgb(255 200 0 / .3); }
```

## Texts

English and Italian are built in (`strings.en`, `strings.it`); `strings` overrides any of them:

```ts
chat.strings = { tagline: 'Here to help', placeholder: 'Ask {title} about your orders…' }
```

## Accessibility

The launcher and every control have names; the panel is a non-modal dialog and the message list a live log; Escape
closes the panel and Ctrl/⌘+J toggles it; question cards answer to keys 1 to 5. The header menu opens with Enter,
Space or the down arrow, moves with the arrows, Home and End, and Escape closes it (not the panel) and gives the focus
back to its button. In the memory view the focus moves to its title; Escape cancels an edit or a confirmation first,
then goes back to the conversation. Voice mode inside the panel is not modal (the page stays usable): the focus moves
to its controls, Escape ends voice mode and gives the focus back to the voice button (the keyboard button gives it to
the message box); the status and the subtitles are announced, in the panel and in the minimized bubble. Minimizing and
ending voice from the orb give the focus to the launcher, whose buttons come up with keyboard focus too. The
full-screen stage of `<evokai-voice>` is a modal dialog: the focus stays inside (Tab cycles), Escape ends voice mode.
With `prefers-reduced-motion` the orb draws one still frame, nothing flies and nothing animates.

## Develop

```bash
npm run demo        # a sample page against a local Evokai (see below)
npm test            # vitest (jsdom): markdown, theme, texts, the elements against a fake Evokai, the React wrapper
npm run build       # ESM + types; fails when the bundle size budget is exceeded
npm run smoke       # Playwright against the running demo (light, dark, a custom theme), screenshots in test-results/
node e2e/voice-panel.mjs [folder] [--skin look.mjs]   # every view and the orb in flight, offline (demo/fake.html)
```

The demo plays your backend: it mints tokens with a server key for a demo user.

```bash
EVOKAI_URL=http://localhost:8787/agent EVOKAI_SERVER_KEY=evk_srv_... EVOKAI_AGENT=assistant \
VITE_EVOKAI_URL=http://localhost:8787/agent npm run demo      # http://localhost:5174 (add it to the allowed origins)
```

**Size budget** (gzip, checked by `npm run build`): the components with Preact 46.5 KB (today about 46.1 KB); the
headless client is a peer dependency (its browser entry about 31 KB, budget 60 KB). The in-browser neural voice and its
runtime load on demand and are never part of these.
