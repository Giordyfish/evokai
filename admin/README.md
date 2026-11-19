# Evokai admin console

A static single-page app (Preact and Vite) served by the service at `{EVOKAI_BASE_PATH}/admin/`.

```bash
npm ci
npm run dev        # against a local service: EVOKAI_URL=http://localhost:8787 npm run dev
npm run typecheck
npm test
npm run build      # to admin/dist, which the service serves
```

## Languages

The console speaks English and Italian. It follows the browser (any `it*` language is Italian, anything else
English) until the person picks a language in Preferences (`#/preferences`, from the signed-in identity at the
bottom of the sidebar or from «Go to»), or with the EN / IT choice on the sign-in page. Dates, numbers, shares and
relative times follow the same language; the date order and the 12 or 24 hour clock can be chosen too.

## Preferences

`src/prefs.ts` holds the signed-in person's preferences: language, date and time format, theme, density, reduced
motion, the mascot (animated, still, hidden), text size, stronger contrast, always-visible focus and underlined links.
They apply at once as attributes on `<html>` (`data-theme`, `data-motion`, `data-contrast`, `data-mascot`,
`data-density`, `data-text`, `data-focus`, `data-links`) that `styles.css` reads, so every page follows them without
code of its own. A copy in `localStorage` (`evk-prefs`) gives the first paint; the service keeps the person's copy
per console identity (`GET`/`PUT`/`DELETE /v1/console/me/preferences`), which wins when it arrives. Animations ask
`RM()` in `src/magic.tsx` at each use, never a value read once at start.

- `src/i18n/en.ts` is the source: it defines every key. `src/i18n/it.ts` has the same type, so a missing or extra
  Italian key is a typecheck error.
- Pages read texts with `t('key', { var })`, counts with `tn('key', n)` or `pl(n, 'n.noun')`, and texts with
  elements in them with `rich('key', { part: <b>..</b> })`. `{name}` is a placeholder.
- `test/i18n.test.ts` checks that both dictionaries have the same keys, plural forms and placeholders, that no
  message has an em dash, and that no page carries hard-coded text (JSX text, text attributes such as `title`,
  `label`, `placeholder`, `hint`, and the first argument of `toast`, `setError`, `confirm`).

**Every new or changed console text is added in English and Italian in the same change.** Error messages that come
from the service are shown as the service wrote them.

### Glossary: kept in English in both languages

Evokai (never translated), skills, MCP, MCP server («server MCP»), playground, prompt, system prompt, token,
SSO, single sign-on, OIDC, issuer, client id, client secret, claim, API key, admin key, server key, evals,
baseline, fallback, guard, override, deployment, backend, endpoint, slug, locale, JSON, CSV, YAML, SKILL.md, PKCE,
redirect URI, scope, tenant, webhook, Microsoft 365, Google Workspace, Microsoft Teams, Slack, the names of providers'
settings (Messaging endpoint, Event subscriptions, Interactivity),
RAG, embedding, chunk, MMR, OCR,
OWASP ids, model ids, control ids such as `input.injection`, setting values (`off`, `monitor`, `enforce`,
`forward_user`, `secret`, `none`, `browser`, `server`), event types, field names shown as code, span, p95, cache. Role names
(`owner`, `operator`, `viewer`) stay as the service stores them.

### Italian choices

| English | Italian |
| --- | --- |
| agent, agents | agente, agenti |
| tool, tools | strumento, strumenti |
| permissions, grants | permessi |
| connections (per-user OAuth) | collegamenti |
| channels (e-mail, Teams, Slack) | canali |
| cap | tetto |
| usage | utilizzo |
| health | stato |
| access (console users) | accessi |
| time to live, retention | conservazione |
| trace, traces | traccia, tracce |
| waterfall (of a trace) | cascata |
| thumbs up, thumbs down | pollice in su, pollice in giù |
| reviewer's note, label | nota di revisione, etichetta |
| subject | soggetto (in labels: «Utente (subject)») |
| turn | turno |
| sign in, sign out | accedi, esci |
| default | predefinito |
| knowledge, knowledge base | conoscenza, base di conoscenza |
| ingest, ingestion | elaborare, elaborazione |
| source (of an answer) | fonte |
| workflow | workflow (invariable) |
| run (of a workflow) | esecuzione |
| step | passo |
| approval | approvazione |
| trigger (how a run starts) | avvio |
| schedule | a orario, pianificazione |

Register: plain, professional Italian, no literal calques, no exclamation marks, quotes as « ». The console never
shows a cost amount in either language: usage is a share of a cap.
