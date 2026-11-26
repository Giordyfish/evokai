# Security of agents

Evokai runs agents that read untrusted text (users, web pages, e-mails, tool output) and act through tools with the
user's own permissions. The security layer (`evokai.guard`) sits around every turn and every server run and applies
the controls below, each **off**, in **monitor** (detect, record a security event, change nothing) or **enforced**
(block, redact or mark, and record). It follows the OWASP Top 10 for LLM Applications 2025, the OWASP Agentic AI
threats and taxonomy, and the NIST AI RMF Generative AI Profile (AI 600-1).

Code: `src/evokai/guard/` (`config` policy and controls, `engine` the turn guard, `injection`, `text`, `pii`,
`secrets`, `content` spotlighting and links, `output` the streaming filter, `tools`, `logfilter`).

## Threat model

| Asset | Threats | Where they come from |
|---|---|---|
| The application's data, reached through MCP tools as the user | an agent tricked into reading more or changing data (excessive agency), data sent to an outside address | direct prompt injection by the user; indirect injection in tool output, pages, documents and e-mails |
| Personal data of customers (codice fiscale, IBAN, card numbers, contacts) | disclosure to the model provider, at rest, in logs, to the wrong person | tool output and messages carry it; answers echo it |
| Secrets (keys, tokens, the deployment's own keys) | leakage in an answer or an event | tool output, misconfigured skills, a model that repeats context |
| The agent's instructions and skills | system prompt extraction | prompt-leak tricks |
| Budgets and availability | unbounded consumption, loops, floods | scripts, a model that repeats a call, very long conversations |
| The browser and the application's pages | unsafe links and images (exfiltration through an address), markdown injection | answers that render links |
| Conversation memory | memory poisoning (instructions stored as facts), recalled text read as instructions | injected text the agent decides to remember; a person's own edits |

Out of scope here: the model provider's own safety, the identity of end users (the application vouches for them), the
application's MCP server authorization (Evokai forwards the user's credential and never widens it), and network
controls (TLS, WAF, private subnets) of the deployment.

Trust boundaries:

```
person ──(text)──▶ browser ──session token──▶ Evokai ──▶ model provider (Bedrock, EU profiles)
                                                │  ▲
                                                ▼  │ untrusted data
                                  MCP tools (as the user) · pages · e-mails · documents
```

Instructions come only from the agent's definition and its skills (trusted, written by the application). Everything
else that reaches the model is data, and the guard treats it so; that includes what a skill's script prints
(`skill_run`), which is tool output like any other, while the script's code is the application's, never the model's.

## Controls

Every control has an id, a default mode and an effect when enforced. The OWASP ids are the 2025 LLM Top 10
(LLM01 Prompt Injection, LLM02 Sensitive Information Disclosure, LLM04 Data and Model Poisoning, LLM05 Improper Output
Handling, LLM06 Excessive Agency, LLM07 System Prompt Leakage, LLM09 Misinformation, LLM10 Unbounded Consumption).

| Control | Mitigates | OWASP | Agentic threats | Default | Enforced |
|---|---|---|---|---|---|
| `input.injection` | prompt injection and jailbreaks in the user's message | LLM01 | T6 intent breaking | monitor | the turn ends with an `error` (`guard`) before the model sees the text; nothing is stored |
| `input.encoding` | zero-width characters, Cyrillic/Greek look-alikes, base64 payloads | LLM01 | T6 | monitor | invisible characters are removed from the input |
| `input.size` | oversized inputs | LLM10 | T4 resource overload | monitor | refused (`guard` error; 413 for server runs) |
| `input.secrets` | API keys, tokens, private keys, passwords and the deployment's own keys in the user's message (the detectors of `output.secrets`) | LLM02 | | monitor | the turn ends with an `error` (`guard`) before the model sees the text; nothing is stored |
| `untrusted.spotlight` | indirect injection: tool output, page text, documents, e-mails read as instructions | LLM01 | T6, T2 tool misuse | **enforce** | content reaches the model between `<<untrusted:...>>` markers with a keyed boundary, and the system prompt says it is data |
| `untrusted.injection` | instructions hidden in that content (knowledge passages included) | LLM01 | T6, T2 | monitor | instruction-like lines are replaced by `[removed by Evokai: instruction-like text]` |
| `pii.model` | personal data sent to the model provider | LLM02 | T15 | monitor | identifiers become reversible tokens (tools and the user still get the real value) or masks |
| `pii.history` | personal data at rest (messages, titles, replay events) | LLM02 | | monitor | identifiers are masked in what is stored; the user still sees them live |
| `pii.logs` | personal data and secrets in logs | LLM02 | | **enforce** | every log line is masked |
| `pii.browser` | personal data echoed in answers | LLM02 | | monitor | identifiers are masked in the answer |
| `output.secrets` | API keys, tokens, private keys, passwords, the deployment's own keys in answers | LLM02 | | **enforce** | replaced by `[SECRET]` before they leave (stream, storage, server-run output) |
| `output.system_prompt` | the agent repeating its instructions | LLM07 | | monitor | the answer stops and ends with a refusal (`guard`) |
| `output.links` | links and images to other sites (exfiltration through an address, phishing) | LLM05, LLM02 | T15 human manipulation | monitor | links keep their text and lose the address; images are dropped; `javascript:` and `data:` never pass |
| `scope` | off-topic use of an agent | LLM09, LLM10 | | monitor | denied topics get the agent's refusal without a model call; topics are added to the prompt |
| `memory.write` | memory poisoning | LLM04 | T1 memory poisoning | monitor | facts with instructions, secrets or identifiers are not stored |
| `tools.policy` | tools an agent should not use, changes without confirmation | LLM06 | T2, T3 privilege compromise | **enforce** | denied tools are not offered and their calls are refused; `confirm` tools always wait for the user |
| `tools.args` | malformed or smuggled arguments | LLM05, LLM06 | T2, T11 | monitor | calls that do not match the tool's JSON Schema go back to the model as errors, never run |
| `tools.rate` | a tool hammered by one user | LLM06, LLM10 | T4 | monitor | calls over the per-minute rate are refused |
| `tools.egress` | tools reaching addresses outside an allow-list (when one is set) | LLM06, LLM02 | T2 | **enforce** | calls whose arguments carry another address are refused |
| `tools.loop` | the same call repeated in a turn | LLM10, LLM06 | T4 | monitor | the repeated call is refused |
| `turn.budget` | tool calls and tokens per turn | LLM10 | T4 | monitor | the turn ends when its budget is spent |
| `abuse.rate` | floods of turns or runs per user | LLM10 | T4 | monitor | 429 `rate_limited` |
| `abuse.concurrency` | many answers at once per user | LLM10 | T4 | monitor | 429 `too_busy` |
| `abuse.length` | very long conversations | LLM10 | T4 | monitor | 429 `conversation_too_long` |

`input.secrets` is in monitor by default: a person pasting a configuration with a key into a chat is often legitimate
(asking for help with it), and refusing it would break existing agents; the event (never the secret itself: the event
has no snippet) shows how often it happens before an owner, or the [system policy](#the-system-policy), enforces it.
Gateways, where text leaves for a provider the organisation does not run, are where enforcing it matters most.

Confirmation of tools that change data (`destructiveHint`, or not `readOnlyHint`) was already enforced by the turn
engine before this layer and stays so: those calls never run without the user's yes, and server runs never get them.
The tool policy adds tools to that list (`tools.confirm`) and removes tools altogether (`tools.deny`). Ask rules add
confirmations call by call ([below](#ask-rules)).

### Ask rules

Borrowed from OpenCode's permissions, with one limit: a rule can only **add** a confirmation (`evokai.guard.ask`). A
rule makes a tool that only reads, or a call with certain arguments, wait for the person like a change:

```yaml
- action: crm_get_customer      # the tool: a whole-value pattern, * any characters, ? one (crm_*)
  argument: customer_code       # optional: the argument the value is matched against (dots reach inside objects)
  resource: "VIP-*"             # a whole-value pattern over it; * (the default) is every call of the tool
  effect: ask                   # ask, or default
  id: key-accounts              # optional, shown in traces (default: <level>:<position>)
  label: {en: "Key accounts", it: "Clienti chiave"}   # why, shown to the person on the confirmation
  tags: ["team:ops"]            # optional: only for subjects with one of these tags (patterns too)
- action: fetch_page
  argument: url
  part: host                    # match the host of a URL in the argument (lower case), not the whole value
  resource: "*.internal"
```

- **Levels.** Three lists: the deployment's defaults (`EVOKAI_ASK_RULES`), the application's (the console's
  Permissions page, `PUT /v1/admin/ask-rules`, `ask_rules` under `application` in the config file) and the agent's
  (`ask_rules` in its definition, the agent editor). Each list is read on its own and in it **the last rule that
  matches a call wins**; a call waits when **any** list says `ask`. A lower level (application below deployment, agent
  below both) can therefore only add confirmations: it never lifts an `ask` of a higher one. A list argument matches
  when one of its items does; a call without the argument does not match a rule on it.
- **Effects.** `ask` makes the call wait for the person; `default` does nothing of its own: it is a narrower exception
  that cancels an `ask` above it **in the same list** (ask for every `crm_*` tool, then `default` for `crm_ping`).
  Neither ever removes the confirmation of a tool that changes data: such a tool always asks. Refused when the rules
  are saved (400 `ask_rules`, with the rule's position and why; a `ConfigError` for the config file and
  `EVOKAI_ASK_RULES`): `allow`, `deny` or any other effect; a `default` rule that names a tool that changes data exactly
  (no wildcard); and a `default` rule with no `ask` above it in its own list whose tool pattern could match the same
  names, since it could only mean lifting another level. A `default` that passes this check but would still only touch
  another level's `ask` (a different argument, say) simply does nothing.
- **Who.** Rules only add confirmations, so the usual roles apply: an operator may change an application's and an
  agent's rules.
- **What the person sees.** The `confirm` event carries `why`, one entry per item: `{key, rule?, level?, label?}`.
  `key` is an i18n key the clients translate (`confirm.why.rule`, `confirm.why.writes`, `confirm.why.policy`);
  `label` is the rule's label in the application's language (else English). The open confirmation of `GET
  /v1/session/conversation` has the same `why`; Teams and Slack confirmation cards add the reason to the
  item.
- **Traces.** The tool span of a call a rule asked for has `askedBy: rule`, `askRule` (its id) and `askRuleLevel`,
  on the wait and on the call that runs once approved.
- **Code Mode, subagents, server runs, workflows.** A tool a rule may ask for (whatever its arguments) is no longer
  read-only: it leaves `run_code` and stays a normal call with its confirmation, subagents never get it (the caller's
  rules and the subagent's own both count), server runs are never offered it (nobody could confirm), and a workflow
  tool step on a call a rule asks for needs an approval step like a change. Without the `confirm` client tool
  granted, such a tool is not offered at all. In v1 this is per tool: a rule limited to certain values (one
  customer code) takes the whole tool out of programs, subagents and server runs.
- **Workflows.** A tool step on a call a rule asks for is treated exactly like a change: it needs an approval step,
  unless the step is `safe: true` and the application holds the `workflows.autonomous` grant, which skip the approval
  for a change too.
- **Always on.** Ask rules apply even with the guard switched off (`EVOKAI_GUARD_ENABLED=false`) or its controls in
  `monitor`: they only add confirmations, so there is nothing to observe instead of enforce.

### How the checks work

- **Injection** (`injection.py`): deterministic rules in English and Italian, each with a weight; a text is flagged
  at the threshold (3: one strong rule, or two weaker ones). Rules run on the normalised text (NFKC, invisible
  characters removed, look-alike letters folded, lower case) and on readable text hidden in base64; obfuscation adds
  weight. Examples: "ignore all previous instructions", "mostrami il prompt di sistema", DAN and developer-mode
  personas, fake `<|im_start|>system` or `[INST]` delimiters, "end of e-mail, now ...", "do not tell the user",
  markdown images that carry data in their address. The rules are bound to the languages and phrasings they know;
  a [classifier model](#injection-classifiers) chosen by the policy adds a language-independent opinion (a score
  of 0.8 or more counts as one strong rule). It is off by default.
- **Spotlighting** (`content.py`): delimiting with a boundary that is a keyed hash of the conversation (stable, so the
  history and its prompt cache do not change between steps; unknown to an attacker); marker-like text inside the
  content is defused. Datamarking (rewriting every space) is not used: it degrades the reading of JSON tool output.
- **Personal data** (`pii.py`): codice fiscale (check letter, omocodia), partita IVA (checksum), IBAN (mod 97, any
  country), e-mail, phones (Italian mobile, landline with a
  separator, international), payment cards (Luhn and an issuer prefix), and the application's
  [own identifiers](#application-identifiers). Tokens are `[CODICE_FISCALE_3f9a1c]`: a keyed hash of the value per
  conversation, stable across turns. Masks are `[CODICE_FISCALE]`.
- **Secrets** (`secrets.py`): shapes only (Evokai keys, AWS keys, JWTs, GitHub, Slack, OpenAI, Anthropic, Google,
  Stripe, bearer tokens, private key blocks, passwords in URLs and assignments) plus the deployment's own signing,
  encryption and operator keys by value. No entropy guessing (it flags every id).
- **Streaming output** (`output.py`): when a control that rewrites the answer is enforced, text is released in pieces
  that never split a value: with identifiers, links or tokens a 64-character tail waits (and any link still open);
  with secrets only, just the last word waits and only when it could start a secret, so ordinary answers stream as
  before. System prompt leakage compares 8-word shingles of the agent's instructions (not of its skills, which it may
  quote) with the answer; ten consecutive words are a leak.

### Knowledge bases

- **Passages are untrusted content.** Each passage `knowledge_search` returns is scanned by `untrusted.injection` (the
  event's `source` is `knowledge:<base>/<document>`) and reaches the model spotlighted under that source, so a poisoned
  document is data, never instructions; `pii.model` applies to them like to any tool output.
- **Access before retrieval.** Documents carry access groups and session tokens and server runs carry the user's
  groups (signed by the application's backend): only chunks of documents the caller may read are searched, so they
  never reach the model, the citations or the logs of someone outside the groups.
- **Fetching by address** (LLM06, SSRF): only allow-listed domains (`EVOKAI_KNOWLEDGE_FETCH_ALLOW`, empty by default),
  public addresses only (loopback, private, link-local and metadata addresses refused), the request pinned to the
  checked address, redirects re-checked, a size cap, no credentials.
- **Signed links**: a citation's link to a kept file is an HMAC of the deployment's signing key with a short life
  (`EVOKAI_KNOWLEDGE_LINK_TTL`, 10 minutes); the file is served with `nosniff`, as a download unless it is a PDF or plain
  text, and a sandboxing Content Security Policy for text.
- **Uploads** are parsed in process without macros, external entities or OCR; Office files are read as zip archives
  of XML with a size limit on what they unpack.
- **Grants**: the `knowledge` grant turns the capability, or one base, off per application.

### Code execution

`code_run` output is tool output: `output.secrets` removes secrets from it, `untrusted.injection` scans it, `pii.model`
protects identifiers in it and it reaches the model spotlighted; the tool policy, argument, rate, loop and egress checks
apply to its calls. The code runs in Amazon Bedrock AgentCore Code Interpreter, one session per call, without internet
([code](code.md)).

### Memory

Writes (the agent's, the person's edits, episodes) pass `memory.write`; episodes are masked before they are stored;
recalled memories reach the model spotlighted as untrusted data (`source=memory`), like tool output. See
[memory](memory.md).

### Red team

Every saved version is attacked by a built-in multilingual suite (direct and indirect injection, prompt extraction,
exfiltration through links and images, excessive agency, personal data and secrets, jailbreak personas, encoding and
obfuscation) graded mostly by deterministic rules, and gets a security score; `redteam_gate: enforce` holds back a
version below `redteam_min_score`. See [red team](redteam.md). Its first run found that an IBAN followed by a word
(«IT60... del cliente») escaped the identifier detector; the pattern now cuts the match back until it validates.

## Configuration

A policy has a base `mode`, per-control `modes` and settings; every field is optional. The mode of a control is the
first one set, from the most specific scope: the agent's mode for that control, the agent's `mode`, the application's
mode for that control, the application's `mode`, the default. So `mode: monitor` on an application turns its
enforced defaults to monitor too; set a control in `modes` to keep it enforced.

In the application YAML (or `security` in `EVOKAI_BOOTSTRAP_APPS`; when the key is present the file is the source of
truth at every start):

```yaml
application:
  security:
    mode: monitor                     # every control not listed below
    modes:
      output.secrets: enforce
      tools.policy: enforce
      untrusted.spotlight: enforce
      input.injection: enforce        # refuse jailbreaks outright
    injection: {threshold: 3, classifier: none, classifier_max_chars: 24000}   # or bedrock_guardrails, prompt_guard
    input: {max_chars: 8000, max_run_chars: 400000}
    untrusted: {input: auto}          # auto: server-run inputs are data, chat messages are not
    pii:
      types: [codice_fiscale, partita_iva, iban, email, phone, card, order_number]   # default: all
      model_action: tokenize          # or mask (irreversible)
    links: {allow: [help.inkwell-books.example]}   # besides relative paths and the allowed origins
    scope:
      topics: ["Inkwell Books orders, returns, shipping and catalogue"]
      deny: [horoscope, "\\bfootball\\b"]
      refusal: "I can only help with Inkwell Books."
    tools:
      deny: [prices_set]
      confirm: [stock_reorder]
      rate_per_minute: {"*": 30, customer_search: 60}
      egress_allow: [api.example.com]   # unset: no egress check
      max_calls_per_turn: 6
      max_tokens_per_turn: 200000
      loop_threshold: 3
    abuse: {requests_per_minute: 30, runs_per_minute: 120, concurrent_turns: 3, max_turns_per_conversation: 200}
  security_event_ttl_days: 90         # default EVOKAI_SECURITY_EVENT_TTL_DAYS (180)
agents:
  - name: mail-triage
    security:
      untrusted: {input: untrusted}   # the e-mail is data (auto already says so for server runs)
      modes: {untrusted.injection: enforce}
```

Admin API (admin key or a console login): `GET /v1/admin/security` (the policy, what applies, the controls with their
OWASP ids, each agent's own policy), `PUT /v1/admin/security` (replace the application's policy; 400 on an unknown
control or setting), `GET /v1/admin/security/events` (filters `control` (a prefix ending in `.` works), `rule`,
`action`, `severity`, `agent`, `subject`, `from`, `to`; pages with `before`), `GET /v1/admin/security/summary`
(counts by rule, control, action, severity). An agent's own policy is the `security` field of its definition
(`PUT /v1/admin/agents/{name}`); a definition without it keeps its version.

Console: the **Security** page of an application: every control with its OWASP ids, the mode that applies (and the
system policy's minimum, when it sets one) and the application's choice; the settings as JSON; each agent's overrides
(editable, saved as a new agent version); the policy's versions and tests and a copy from another policy; the events
with filters, their counts by rule and a CSV export.

### The system policy

One policy for the whole deployment (code: `evokai.policies`), with the same shape as an application's (`security`
and `data_protection`). It can only make things stricter:

- **Minimum modes.** Its `modes` give each control a minimum (`off < monitor < enforce`), its `mode` the minimum of
  every control it does not name. The effective mode is the stricter of the minimum and the mode resolved as above
  (agent, application, default). An application that turns `input.injection` off still has it enforced when the
  system policy says `input.injection: enforce`; a minimum below the resolved mode changes nothing. The kill switch
  (`EVOKAI_GUARD_ENABLED=false`) still turns everything off.
- **A data protection profile** that every application's processors must meet **as well as** the application's own
  ([data protection](data-protection.md#the-system-policys-profile)).
- **Default settings** (personal data types, scope deny lists, limits, the classifier...). They are read before the
  application's and the agent's: **defaults, not floors**. An application that sets its own `scope.deny` replaces the
  system's list, it does not add to it. What must hold everywhere is a minimum mode, not a setting.

Only the deployment's owners see and change it: the operator key, or a console login that owns every application
(single sign-on role `owner` on `*`). That is the role that already creates applications and manages sign-in; an
application's owner may not weaken or read rules meant for every application. Routes (deployment owners):
`GET`/`PUT /v1/console/system-policy` (body `{security, data_protection, note, force}`), and the versions, diff,
restore, copy and tests routes below under the same prefix. Console: **System policy** in the navigation. A save is in
force on the instance that made it at once and on the others within 15 seconds (each reloads it). The system policy's
profile cannot declare what browsers send (`browser`, 400 `system_browser`): that is each application's declaration.

### Policy versions

Every save of a policy, whatever the path (`PUT /v1/admin/security`, `data_protection` in `PATCH /v1/admin/application`,
the config file at start, the console, a restore, a copy), writes an immutable version: its number, the content
(`security` and `data_protection` together), who saved it (as the audit log names them; `config` for the config file),
when, and an optional note. A save that changes nothing writes no version, so restarting with the same config file adds
none. The current version is what applies; there is no pinning to an old one. A restore saves an old content again as a
new version.

| Route (an application's; the system policy's under `/v1/console/system-policy`) | |
|---|---|
| `GET /v1/admin/policy/versions` | the versions, newest first: `number`, `createdAt`, `createdBy`, `note` |
| `GET /v1/admin/policy/versions/{number}` | one version with its `content` |
| `GET /v1/admin/policy/diff?from=1&to=3` | `changes`: `{path, change, before, after}` per setting (`to` defaults to the current version; a path is a list of keys, lists compare whole) |
| `POST /v1/admin/policy/versions/{number}/restore` | owner; `{note, force}` |
| `POST /v1/admin/policy/copy` | owner; `{system: true}` or `{application: "<slug>"}`, plus `{note, force}`: copies that policy's content into this one as a new version |

Copy takes only a policy the caller can see: the system policy for deployment owners, another application for a
console login with a role on it. An admin key sees only its own application. `PUT /v1/admin/security` takes `note`
and `force` as query parameters. Existing applications got version 1 (their policy at the upgrade) from the migration.

### Policy tests

A policy test is a text and the outcome the guard must give it: for the whole text (`outcome`) and/or per control
(`controls`), each `allowed` (no control acts), `flagged` (a control records or changes the text, none refuses it) or
`blocked` (a control refuses it). A test may name the controls the input checks run: `input.size`, `input.encoding`,
`input.secrets`, `input.injection`, `scope`, and `untrusted.injection` when the policy treats the person's input as
data; a [gateway](gateway.md)'s tests run the gateway's own checks, and may also name `pii.model`. They run those checks only, offline, with the effective policy (an application's: its own with the system
policy's minimums and defaults; the system policy's: what an application that sets nothing gets): never a model and
never a classifier (the injection classifier and `input.moderation` are not asked), so they cost nothing and store
no event. A blocking control ends the checks as in a turn: the controls after it are not reached.

They run on demand (`POST /v1/admin/policy/tests/run`, viewer) and before every save of the policy's security part: a
save, restore or copy whose tests fail is refused with 409 `policy_tests_failed` and the `failures` (each with what it
expected and what it got), unless the request says `force: true`. The config file applied at start is not held back
by tests (it is the source of truth of a deployment); a change of `data_protection` alone does not run them (the input
checks do not read it). A system policy save runs the system policy's own tests, not every application's.

`GET`/`POST /v1/admin/policy/tests`, `PUT`/`DELETE /v1/admin/policy/tests/{id}` (owner): `{name, input, outcome?,
controls?}`; names are unique per policy, and a policy has 200 tests at most (409 `too_many_tests`: every save
runs them all). The console shows the tests next to the versions, runs them, and shows the
failures of a refused save with a **Save anyway** button.

### Application identifiers

An application declares its own codes (an order number, a library card, a contract code) in `application.identifiers`
of its config file. Each one is a type of personal data like `iban`: `pii.model` tokenises it (`[ORDER_NUMBER_3f9a1c]`,
tools still get the real value), `pii.history`, `pii.logs` and `pii.browser` mask it (`[ORDER_NUMBER]`), its name may
appear in `pii.types` and in security events, and speech says it the way it asks. Built-in types are unchanged.

```yaml
application:
  identifiers:
    - name: order_number            # its type: 2-40 lower case letters, digits, _; not a built-in type
      label: ORDER_NUMBER           # in masks and tokens; default: the name in upper case
      match:                        # any entry recognises a value
        - pattern: 'ORD-\d{6}'      # a regular expression (Python), never inside a longer word or number
      speech:
        read: word                  # word (default), spell, group, omit or verbatim
        words: {en: "the order number", it: "il numero d’ordine"}
    - name: library_card
      match:
        - digits: 12                # a number: 12 digits (or [shortest, longest])
          grouped: false            # written together only (default: groups with spaces or dashes too)
        - digits: 14
          context: 'library card|tessera'   # only with this text (any case) just before the value
          context_chars: 30                 # how far before it is looked for (default 30)
        - digits: 10
          check: luhn               # only values whose digits pass the Luhn checksum
      speech: {read: spell}
```

- **match**: a `pattern` (case-insensitive unless `ignore_case: false`; Evokai adds the word boundaries) or `digits`.
  A number is found the way the built-in numbers are: written together or in groups separated by spaces or dashes,
  never a piece of a longer number, a decimal or a number written after `IT`. `check` and `context` narrow either
  kind.
- **Precedence**: on the very same text a built-in type (IBAN, codice fiscale, e-mail, partita IVA, card) wins over a
  declared identifier, unless the declared match was confirmed by its `context`; a declared identifier wins over a
  phone number. Otherwise the earlier, then the longer, match wins, as for built-in types.
- **speech**: `word` says `words` in its place (the locale's, else its language's, else English, else the name),
  `spell` reads it character by character (`O R D 1 2 3 4 5 6`), `group` in groups of `size` characters (default 3:
  `ORD 123 456`), `omit` says nothing, `verbatim` reads it as written. Speech uses the same `match`; `speech.match`
  adds entries used when speaking only (a looser shape, so that nothing that looks like the code is ever read aloud).
  The browser client speaks the server's `say` events, so this holds for every voice engine.
- **Where it applies**: in the application's turns and server runs, the application's identifiers. Text masked
  outside a turn (log lines, traces, memories, workflow records) is masked with the identifiers of every application
  the service has loaded.
- The file is the whole set: an identifier left out of it is no longer recognised at the next start. A `pii.types`
  entry (application or agent) that is neither built-in nor declared stops the start; the console refuses it too
  (`400 unknown_pii_types`). Patterns run on every text the guard reads: keep them simple and anchored to a literal
  part.

Deployment: `EVOKAI_GUARD_ENABLED=false` turns every control off at once (an emergency switch); the classifiers'
settings are below; `EVOKAI_SECURITY_EVENT_TTL_DAYS` (180) is the default retention of events.

## Injection classifiers

The rules of `input.injection` and `untrusted.injection` know English and Italian. An attacker who writes in German or
paraphrases passes them. A classifier model recognises attacks by meaning, in many languages, and the policy chooses
one per application and per agent:

```yaml
security:
  injection: {classifier: bedrock_guardrails, classifier_max_chars: 24000}
```

| `injection.classifier` | What it is |
|---|---|
| `none` (the default) | rules only |
| `bedrock_guardrails` | the prompt attack filter of Amazon Bedrock Guardrails, through `ApplyGuardrail` |
| `prompt_guard` | Meta Llama Prompt Guard 2 86M, run on the service's own CPU |
| `azure_prompt_shields` | Azure AI Content Safety Prompt Shields (`text:shieldPrompt`, API 2024-09-01): the person's message as the user prompt (jailbreaks), untrusted content as documents (indirect attacks); `EVOKAI_AZURE_CONTENT_SAFETY_ENDPOINT` and `_KEY`; billed per 1,000-character text record |
| `model_armor` | Google Model Armor `sanitizeUserPrompt` with a template whose prompt injection and jailbreak filter is on (`EVOKAI_MODEL_ARMOR_TEMPLATE`, the token from `EVOKAI_MODEL_ARMOR_TOKEN` or Application Default Credentials), at the template location's regional host |
| `mistral_moderation` | the `jailbreaking` category of Mistral's moderation model (`/v1/moderations`, `MISTRAL_API_KEY`, `EVOKAI_MISTRAL_MODERATION_URL` for its EU endpoint); Mistral's guide documents the category, its API reference examples do not show it yet: without it the score is 0 |
| `custom` | a third-party classifier loaded from `EVOKAI_GUARD_CLASSIFIER=module:factory` (an object with `async score(text) -> float`) |
| `default` | the deployment's choice: `EVOKAI_GUARD_CLASSIFIER_DEFAULT`, else `custom` when configured, else the first available of Bedrock Guardrails and Prompt Guard. The old `true` and `false` are read as `default` and `none` |

An unknown value is refused (400). The console's Security page shows the choice, what applies, and which classifiers
this deployment can actually use, with the reason when it cannot (`GET /v1/admin/security`: `classifiers` with `id`,
`available`, `reason`, and `defaultClassifier`; `moderationClassifiers` the same for moderation).

**The data protection profile picks.** Every classifier has an entry in the provider catalog (who runs it, where, its
price; [data protection](data-protection.md)). When the application's profile excludes it (a jurisdiction rule keeps the
US-controlled ones out, for example), no text is sent: the turn records `classifier_unavailable` with the reason
(`data_protection: jurisdiction outside`) and goes on with the rules. The local Prompt Guard runs inside Evokai and
meets every profile.

### Moderation is not injection detection

A moderation model judges harmful content (hate, violence, sexual content, self-harm); a prompt injection is usually
polite text that none of those categories flag. So moderation has its own control, `input.moderation` (off by default),
on the person's message, with its own classifier:

```yaml
security:
  modes: {input.moderation: enforce}
  moderation: {classifier: openai_moderation, threshold: 0.8}
```

| `moderation.classifier` | What it is |
|---|---|
| `azure_content_safety` | Azure AI Content Safety text analysis (`text:analyze`, four severity levels: hate, self-harm, sexual, violence) |
| `openai_moderation` | OpenAI's `omni-moderation-latest` (free; `OPENAI_API_KEY`) |
| `mistral_moderation` | Mistral's moderation categories (sexual, hate, violence, dangerous and criminal content, self-harm; the advice and personal data categories are left to Evokai's own controls) |
| `model_armor` | Model Armor's responsible AI filters of the template |
| `llama_guard` | a Llama Guard the deployment serves behind an OpenAI-compatible chat API (`EVOKAI_LLAMA_GUARD_URL`, `EVOKAI_LLAMA_GUARD_MODEL`, optional `EVOKAI_LLAMA_GUARD_KEY`): `unsafe` scores 0.95 |

A message at or over the threshold is a `harmful` event; in enforce mode the turn is refused before the model sees it.
Moderation classifiers fail open like the injection ones, and the data protection profile applies to them the same way.

**How it counts.** The rules run first. When they already reach the threshold the classifier is not asked (its answer
could not change the outcome, and it may be billed); otherwise its score is added: 0.8 or more is one strong rule
(rule `classifier`, 3 points). It applies to the person's message (`input.injection`) and to untrusted content
(`untrusted.injection`: tool output, the page the user is on, a server run's input and its attachments). In enforce
mode, content the classifier flags loses the lines that read as instructions, or all of it when no line does
(`[removed by Evokai: instruction-like text]`). The event's details carry the classifier's score.

**It never blocks a turn by itself failing.** A missing model, an error or a timeout (1.5 s for Bedrock, 3 s for
Prompt Guard, configurable) lets the turn go on with the rules alone and records `classifier_unavailable` (severity
low, once per turn, the reason in the details). The console shows the last failure next to the classifier.

**Budget.** `injection.classifier_max_chars` (24,000 by default) is what a turn or a run may send to the classifier,
the message and the untrusted content together. What is over is checked by the rules only and records
`classifier_capped` once. Tool output is already clipped to `EVOKAI_TOOL_RESULT_CHARS` (12,000) before.

### Amazon Bedrock Guardrails

- **What is sent**: only the texts above, cut into pieces of at most 1,000 characters that overlap by 100 (cuts fall on
  spaces), each in its own `ApplyGuardrail` request (up to 8 at a time) with `source: INPUT` and one text block with
  the `guard_content` qualifier. Nothing else of the conversation, no system prompt, no instructions.
- **Score**: a `PROMPT_ATTACK` filter detected (or blocked) with confidence `LOW`, `MEDIUM` or `HIGH` scores 0.5,
  0.8 or 0.95; the text's score is the highest of its pieces. Other filters are ignored.
- **Why it is called this way** (AWS documentation, read 15/11/2026): prompt attacks are an *input* filter (only an
  input strength exists), so every text goes as `INPUT`, untrusted content too. Inside `Converse` and `InvokeModel`
  the prompt attack filter never evaluates tool results (`toolResult`) or tool definitions, and with `InvokeModel` it
  evaluates only text inside input tags (without tags nothing is filtered for prompt attacks). Attached to model calls,
  then, a guardrail would not see tool output, documents or e-mails at all. `ApplyGuardrail` evaluates exactly the
  text blocks it receives: sending untrusted content as its own blocks is what makes it evaluated. AWS describes the
  filter as detecting user prompts (jailbreaks, prompt injection, prompt leakage on the Standard tier) and does not
  publish detection figures for injections hidden in third-party content: treat it as a strong second opinion on
  that content, not a guarantee.
- **The guardrail**: only the content filter `PROMPT_ATTACK`, input strength `HIGH`, input action `BLOCK` (the
  answer is read, nothing is blocked by AWS), no output filter, Standard tier, which requires a cross-Region
  guardrail profile: the EU profile keeps processing in EU Regions. A published version, never `DRAFT`.
- **Languages**: the Standard tier supports more than 80 languages for prompt attacks, optimised among others for
  English, Italian, German, French, Spanish, Portuguese, Dutch and Polish; the Classic tier only English, French and
  Spanish.
- **Cost**: billed per text unit of up to 1,000 characters (content filters: the same price on both tiers), so a
  piece never exceeds one unit. Each call is a usage record of kind `guard` (model `bedrock_guardrails`, the units in
  `input_tokens`, the characters sent in `characters`) and counts against the application's and the user's caps like
  model calls; usage reports return the units as `textUnits`. The console shows only shares, never amounts. At the
  default budget a turn sends at most about 27 units (24,000 characters plus the overlaps).
- **Latency**: one round trip per piece, pieces in parallel; typically a few hundred milliseconds per turn.
- **Settings**: `EVOKAI_GUARDRAIL_ID`, `EVOKAI_GUARDRAIL_VERSION`, `EVOKAI_GUARDRAIL_REGION` (default
  `EVOKAI_AWS_REGION`), `EVOKAI_GUARDRAIL_TIMEOUT_MS`; the task role needs `bedrock:ApplyGuardrail` on the guardrail
  and on the guardrail profile `eu.guardrail.v1:0` in each of its destination Regions (from eu-north-1:
  eu-central-1, eu-west-1, eu-west-3, eu-north-1). Credentials come from the default AWS chain.

### Llama Prompt Guard 2 (local)

- **What it is**: Meta's Llama Prompt Guard 2 86M, an mDeBERTa-v3 classifier (benign or malicious) with a 512-token
  window, evaluated by Meta on English, French, German, Hindi, Italian, Portuguese, Spanish and Thai. It looks for
  explicit attempts to override instructions (jailbreaks and injections); it does not judge whether content is
  harmful. No text leaves the deployment and nothing is billed or recorded as usage.
- **How it runs**: a text is tokenised once and cut into 512-token windows overlapping by 64 (at most 32 per text),
  scored one at a time on a dedicated thread with ONNX Runtime (`EVOKAI_PROMPT_GUARD_THREADS`, 2), never on the
  event loop; the text's score is the highest malicious probability. The model loads when the service starts (and is
  run once), so the first turn is not slow. At `EVOKAI_PROMPT_GUARD_TIMEOUT_MS` (3 s) no further window starts: the
  windows scored count, the rest records `classifier_unavailable` (timeout).
- **Latency** (measured 15/11/2026 with ONNX Runtime 1.32 on an x86 laptop CPU, a model of Prompt Guard 2's exact
  architecture and size; a Graviton task is slower per thread):

  | Threads | 64 tokens (a chat message) | 512 tokens (a full window, about 2,000 characters) |
  |---|---|---|
  | 1 | 190 ms (int8: 85 ms) | 1.85 s (int8: 1.13 s) |
  | 2 | 105 ms (int8: 50 ms) | 1.06 s (int8: 0.67 s) |
  | 4 | 65 ms (int8: 35 ms) | 0.64 s (int8: 0.44 s) |

  A person's message costs tens of milliseconds; long tool output costs about a second per 2,000 characters on two
  threads, added to the turn. Use the `--int8` export, give it 4 threads on a task with the CPUs for it, and lower
  `classifier_max_chars` (for example 6,000) for agents that read long documents.
- **Not in the default image.** It is an optional extra (`evokai[prompt-guard]`: ONNX Runtime, `tokenizers`,
  NumPy). The model is gated and never downloaded by Evokai from anywhere but the official repository.

To enable it:

1. On [huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M),
   with the account that will download it, read and accept the **Llama 4 Community License** and the Acceptable Use
   Policy, then create a read token (`HF_TOKEN`). Never use a mirror or a re-upload.
2. Export it once on a workstation:
   `HF_TOKEN=... uv run scripts/export-prompt-guard.py --model meta-llama/Llama-Prompt-Guard-2-86M --out models/prompt-guard`
   (or `--model <folder>` with files you downloaded; `--int8` for a model about a third of the size). The script
   writes `model.onnx`, `tokenizer.json`, `config.json`, the license files and a `NOTICE`, and checks ONNX Runtime
   against PyTorch.
3. Build the image with it: `scripts/build-image.sh <repo> <tag> --with-prompt-guard models/prompt-guard`
   ([deployment](deployment.md#an-image-with-llama-prompt-guard-2)); or, without Docker, `pip install
   'evokai[prompt-guard]'` and `EVOKAI_PROMPT_GUARD_MODEL=models/prompt-guard`.
4. Choose it: `injection: {classifier: prompt_guard}` (or `EVOKAI_GUARD_CLASSIFIER_DEFAULT=prompt_guard` and
   `classifier: default`).

With `transformers` and `torch` installed, `EVOKAI_PROMPT_GUARD_MODEL` may also be the official folder or the repo id
itself (with `HF_TOKEN`); the image does not carry them.

**License.** Shipping the model inside an image is distributing Llama Materials: keep the license text and the
`NOTICE` («Llama 4 is licensed under the Llama 4 Community License, Copyright © Meta Platforms, Inc. All Rights
Reserved.») with it (the export puts them in the folder, the image keeps them in `/opt/prompt-guard`), say «Built with
Llama» in the product's documentation or about page, follow the Acceptable Use Policy, and ask Meta for a license
above 700 million monthly active users.

## Security events

Every decision is also a span of the turn's trace (control, rule, action, mode, severity; never the snippet), so the
console's Traces page shows where in the turn it happened and filters the turns that had one.

Every block, redaction, removal, tokenisation and monitor finding becomes an event in `evokai_security_events`:
application, time, control, rule, action (`logged`, `blocked`, `redacted`, `removed`, `tokenized`), mode, severity,
direction, agent, subject, conversation, turn, tool, a snippet and details (counts, rules, reasons). The snippet is
masked (identifiers and secrets replaced) and clipped to 200 characters when it is recorded: no secret or raw personal
data is ever stored in an event. Personal data findings are one event per direction per turn, with counts by type.
Events are stored at the end of the turn (before `done`) and logged at once as `security_event` records
(control, rule, action, mode, severity, application, agent, turn, tool; never the snippet). Events are deleted after
the application's `security_event_ttl_days`, and a subject's events are de-identified by the right to be forgotten
(see [privacy](privacy.md)).

## Defaults in practice

An application whose config has no `security` section gets the defaults, and nothing it did before is refused. Take
the example bookshop of `config/bookshop.yaml` without its `security` section:

- the `bookshop` chat agent: injections, identifiers, links and abuse are recorded (monitor); secrets never leave in
  an answer; the tool policy is enforced but allows every tool (nothing is denied or added to `confirm`), so its MCP
  tools and their confirmations work as before; tool output reaches the model marked as data; log lines are masked;
- a `mail-triage` server run: the e-mail body and attachments are untrusted data (spotlighted and scanned,
  `untrusted.input: auto`), identifiers are recorded but still sent (its `customer_search` needs the customer's
  e-mail address), and its structured output passes the secret check.

Sensible next steps, once the events show no false positives: `input.injection: enforce`, `untrusted.injection:
enforce` for `mail-triage`, `links: {allow: [help.inkwell-books.example]}` with `output.links: enforce`, and
`pii.model: enforce` (tokenize) for `mail-triage` (its tools still receive the real values).

## Limits

- Heuristics are not a proof: a novel phrasing can pass and an unusual message can be flagged. That is why the
  defaults record before they block, spotlighting is always on, and the real protection of data is structural (the
  user's own credential on every tool, confirmations for changes, read-only tools in server runs).
- Rate, concurrency and tool-rate counters are shared by every instance only with `EVOKAI_REDIS_URL` (Redis or
  Valkey, [shared limits](configuration.md#shared-limits-across-instances)); without it each instance counts in its own
  memory and with several tasks the effective limits multiply by the number of tasks. While Redis does not answer, the
  limits fall back to that per-instance count (fail open, logged as `shared_state_unavailable`), never to a refusal.
  The rate windows are fixed minutes, so a burst across a minute's edge can reach twice the rate.
- Monitor-mode findings on the answer are computed at the end of each model step; enforced rewriting happens while
  streaming. System prompt leakage is detected after ten repeated words, so those words may already be on screen when
  the answer stops.
- Masking applies to identifiers with a recognisable format. Names, addresses and free-text personal data are not
  detected; use `pii.history` with `conversation_ttl_days` and the erase API for those.
- Spotlighting relies on the model following the system prompt; it lowers, not removes, the risk of indirect injection.
- A classifier is a second opinion, not a proof either: it has false negatives (an attack it was not trained on) and
  false positives (a document that discusses prompts). Start with `untrusted.injection` in monitor and read the
  events before enforcing.
- Pending confirmations keep the real arguments of the waiting tool calls until they are decided (they must run as
  confirmed).

## NIST AI RMF and AI 600-1

The controls map to the Generative AI Profile's risks: **Information Security** (prompt injection, indirect
injection, secrets, egress: `input.*`, `untrusted.*`, `output.secrets`, `tools.*`), **Data Privacy** (`pii.*`, retention,
erase and export), **Information Integrity** (spotlighting, scope) and **Value Chain and Component Integration**
(SBOM and image signing, see [deployment](deployment.md)). In RMF terms the policy is part of MANAGE (2.2, 2.4: the
mechanisms to supersede, disengage or deactivate, here `EVOKAI_GUARD_ENABLED` and per-control modes), the security
events and the console's counts serve MEASURE (2.7 security and resilience, 3.1 tracking of risks over time), and
the audit log of console changes ([console](configuration.md#console-sign-in-and-roles)) supports GOVERN 1.4 and 4.2.

### Workflows

Workflow steps run under the same policy as the application's agents ([workflows](workflows.md#security-and-limits)):
agent steps are server runs with their guard; tool steps go through the granted toolbox and the tool checks
(`tools.policy`, `tools.args`, `tools.egress`, `tools.rate`), and a tool that changes data needs a person's approval
unless the definition marks it `safe` and the application is granted `workflows.autonomous`; `notify` webhooks are
SSRF-safe (allow-listed domains, public addresses only, pinned, no redirects) and HMAC-signed; inbound webhooks need an
HMAC signature with a recent timestamp. Conditions and templates are parsed by a small evaluator over JSON values, never
`eval`: no attributes, no calls but a fixed list of filters, no underscore names, bounded sizes.

### Connections and channels

[Connections](connectors/connections.md) let an agent act as a person in Microsoft 365, Google Workspace, GitHub,
Linear, Notion or Slack: the flow is
an authorization code with PKCE and a single-use, ten-minute state kept server-side and bound to the person; tokens are
encrypted and refreshed, only the configured families' scopes are asked, write tools (drafts, events) always wait for
the person's confirmation and are never offered to server runs, and «disconnect», «revoke all» and erasure delete the
tokens. Providers are off until granted, and each tool family can be revoked.

[Channels](channels/README.md) verify every delivery before reading it (an HMAC signature for inbound mail and Slack,
the Bot Framework's JWT for Teams), answer a repeated delivery once, rate-limit per person and per channel, and post
Teams replies only to allowed service hosts. An e-mail is untrusted input of a server run (scanned, marked as data,
read-only tools only, attachments within caps); loops are cut on automatic mail, lists, bounces, daemons and the
agent's own address, and replies to one address are capped per hour. A thread belongs to the subject it was mapped
to, so a reply or a button from someone else never continues another person's conversation. Channels are off until
granted, and an agent must list a channel to be reached through it.

## Related controls elsewhere

- No conversation is ever stored at a model provider (`evokai.llm.policy`), only EU inference profiles are allowed by
  the deployment allow-list, and an application may declare its data residency ([privacy](privacy.md)).
- Session tokens, origins, CORS, headers and the console's authentication are reviewed in
  [security-assessment](security-assessment.md).
