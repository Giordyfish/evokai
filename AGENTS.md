# Instructions for agents working on Evokai

Repository content (code, comments, docs, commit messages) is in English. Never write the em dash character
(U+2014) anywhere: `scripts/ci.sh` fails on it. Use a comma, a colon, parentheses or two sentences.

Run the gates of what you touched (`scripts/ci.sh service|python|client|ui|console|worker`) and count failures from
the test runners' summary lines before saying they pass. Commits are signed off (`git commit -s`, the DCO in
[CONTRIBUTING.md](CONTRIBUTING.md)) by the person who submits them, and every pull request describes the problem, the
approach and how it was tested.

## The admin console speaks English and Italian

The console (`admin/`) has two languages, English (the source) and Italian, and both are maintained from now on.

- Every new or changed text a person reads in the console (labels, buttons, headings, help text, empty states,
  errors, confirmations, toasts) goes in `admin/src/i18n/en.ts` **and** `admin/src/i18n/it.ts` in the same change,
  and the page reads it with `t('key')`, `tn('key', n)` for a count or `rich('key', { part })` for a text with
  elements. Never write the text straight into a page.
- Dates, numbers and shares go through `admin/src/format.ts` (or `num` from `admin/src/i18n`), never
  `toLocaleString('en')` or a hand-made month name.
- Translate only what needs translating. Terms of art stay in English in both languages (see the glossary in
  `admin/README.md`): skills, MCP, MCP server, playground, prompt, system prompt, token, SSO, OIDC, API key, admin
  key, server key, evals, guard, setting values such as `off`, `monitor` and `enforce`, JSON, CSV, YAML, model ids,
  control ids like `input.injection`, OWASP ids, field names shown as code. In Italian prose an agent is an
  «agente» and tools are «strumenti». Evokai is never translated.
- Italian: plain, professional, no literal calques, no exclamation marks, quotes as « ».
- The console never shows what was spent: usage is a share of a cap. The only figures with a currency are the caps
  an admin sets on the Limits page (the maxima, in the application's currency).
- `npm test` in `admin/` fails when a key exists in one language only, when the placeholders differ, when a message
  has an em dash, and when a page carries hard-coded text (`admin/test/i18n.test.ts`); `npm run typecheck` fails
  when the Italian dictionary misses a key.
