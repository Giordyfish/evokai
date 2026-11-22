# Prompt modules

A prompt module is a reusable, versioned piece of an agent's system prompt that your application switches on or off,
and fills with parameters, for each session. The agent's instructions stay the same for everyone; a module adds what
differs between sessions (how long the answers are, how much guidance a new person needs, which shelf a clerk looks
after), chosen by your backend when it mints the session token. The browser can never change it.

## What a module is

A module is an application prompt (`PUT /v1/admin/prompts/{name}`, the console's **Prompts** page, or the `prompts:`
section of the application YAML) that also carries:

- **`parameters`**: a JSON Schema of the values a session may give, in the restricted subset below;
- **`description`**: what it is for (shown in the console);
- **`data`** (optional): a lookup table of texts, part of the version (at most 256 KB), that the template reads by a
  choice parameter. A session then picks an entry by name without sending any free text.

Every change of text, schema, description or data is a new version. A prompt without `parameters` is a plain prompt; an
agent may still list it as a module without parameters. A save that leaves out `parameters`, `data` or `description`
keeps them as the latest version has them (send `parameters: null` to make a module a plain prompt), and a new version
whose parameters no longer fit the values an agent gives it (an agent that follows the latest version) is refused with
400 `module_in_use`.

```yaml
prompts:
  - name: tone
    description: How long the answers are.
    text: "{{lookup data style}}"
    parameters:
      type: object
      properties:
        style: {type: string, enum: [brief, detailed], default: brief}
    data:
      brief: "Keep answers to one or two sentences unless the user asks for more."
      detailed: "Give the reasons and the figures behind each answer, in a few short paragraphs."
  - name: reader_level
    description: Extra guidance for people new to the back office.
    text: "{{#if beginner}}The user is new to the back office: explain each step.{{/if}}"
    parameters:
      type: object
      properties:
        beginner: {type: boolean, default: false}
```

## The parameter schema

The schema is an object with at most 20 properties, an optional `required` list, and `additionalProperties` forced to
`false`. Each property is one of:

| Kind | Schema | Console control |
|---|---|---|
| Choice | `{type: string, enum: [...]}`, 1 to 50 values of at most 100 characters | select |
| Short text | `{type: string, maxLength: n}` (n at most 200, required), optional `pattern` | text input |
| Number | `{type: integer}` or `{type: number}`, optional `minimum` and `maximum` | number input |
| Yes/no | `{type: boolean}` | switch |
| Multiple choice | `{type: array, items: {type: string, enum: [...]}, maxItems: n}` (n at most 200) | switches |

Each property may have a `default` (it must fit the property) and a `description`. Anything else (nested objects,
`$ref`, free strings without `maxLength`) is refused with 400 `module_schema`. The names `date`, `app` and `data` are
reserved.

## The template

A module's text is a [Handlebars](https://handlebarsjs.com/guide/) template, rendered with
[pydantic-handlebars](https://pypi.org/project/pydantic-handlebars/) (the engine of pydantic-ai's `TemplateStr`):

- `{{name}}` a parameter, `{{#if flag}}...{{else}}...{{/if}}`, `{{#each list}}{{this}}{{/each}}`;
- `{{lookup data style}}` the entry of the data table picked by a choice parameter;
- `{{date}}` today in the application's time zone, `{{app}}` the application's name.

A template holds at most two `{{#each}}` blocks and renders to at most 64,000 characters; a module that cannot be
rendered is left out of the prompt (with a warning in the log). A free-text `pattern` has at most 100 characters and no
quantified group that holds a quantifier (`(a+)+`), which could backtrack for minutes.

Values are inserted as they are: nothing is HTML-escaped (`R&D <team>` stays `R&D <team>`). When the prompt is saved the
template is checked against its schema: a variable it does not declare (a typo such as `{{nmae}}`) is refused with 400
`module_template`, and a data table that is not a map of short keys to texts with 400 `module_data`.

Module texts without `{{` keep the older single-brace placeholders. Agents' instructions (inline, or a prompt they
reference with `prompt:`) are never Handlebars: `{date}` and `{app}` are replaced, `{{` and `}}` become single braces
as they always did, and every other brace is left as written, so JSON examples in instructions stay intact.

## Agents list their modules

```yaml
agents:
  - name: bookshop
    # ...
    modules:
      - {name: tone, session: true}
      - {name: reader_level, session: true}
      - {name: house_rules}                # always on, a session cannot change it
      - {name: shelf, version: 3, params: {section: poetry}}
```

Each entry names a prompt, and optionally:

- `version`: a fixed version (it applies to running conversations too); without it the latest version when a
  conversation starts, then **pinned** for that conversation (a later version of the prompt does not change a
  conversation under way);
- `on` (default `true`): whether the module is in the prompt unless the session switches it;
- `params`: the agent's values for its parameters (the schema's defaults fill the rest; required parameters may be left
  to the session);
- `session` (default `false`): whether a session token may switch it or change its parameters. A module the agent
  locks later gets the agent's own state back in running conversations; one that is on and locked must have every
  required value (400 `module_params` otherwise).

An agent lists at most 20 modules, each once. Saving an agent checks that every module exists (400 `unknown_module`)
and that its values fit (400 `module_params`). Module texts go right after the agent's instructions, in this order and
separated by a blank line, before the preloaded skills: they are part of the stable, cacheable part of the prompt.

## Choosing modules per session

Your backend, holding the server key, chooses at token mint:

```http
POST /v1/session-tokens
{"subject": "u-42", "agent": "bookshop",
 "modules": {"tone": {"params": {"style": "detailed"}}, "reader_level": {"on": true, "params": {"beginner": true}}},
 "locale": "it-IT"}
```

```ts
await evokai.mintSessionToken({ subject: user.id, agent: 'bookshop', modules: { tone: { params: { style: 'detailed' } } } })
```

```python
server.mint_session_token("u-42", "bookshop", modules={"tone": {"params": {"style": "detailed"}}}, locale="it")
```

Precedence: the schema's defaults, then the agent's `params`, then the session's `params`; the session's `on` over the
agent's. On a continued conversation (`conversationId`), `modules` given replaces the earlier choice (pinned versions
stay); omitted keeps it. The same `modules` and `locale` are accepted by server runs (`POST /v1/agents/{name}/run`).

The mint refuses:

| Status | Code | When |
|---|---|---|
| 400 | `unknown_module` | the agent does not list it |
| 400 | `module_locked` | the agent's entry has `session: false` |
| 403 | `module_not_granted` | the application lacks the grant `module:<name>` (granted by default) |
| 400 | `module_params` | a value does not fit, or a required one is missing (the message names the parameter, never its value) |
| 400 | `module_param_rejected` | the guard refused a free-text value |
| 400 | `unknown_locale` | the locale's language has no shipped pack |

`locale` is a BCP 47 tag; speech rules (numbers read aloud, spoken yes and no) and the messages the person reads use the
shipped pack of its language, today `en` and `it`. Without it the session follows the application's `locale`.

## Who may set what

| Who | What |
|---|---|
| Admin (operator role or higher) | prompts, their schemas and data, the agents' module lists and defaults, the `module` grants |
| Application backend (server key) | per session: `on` and `params` of the modules the agent marks `session: true`, and the locale |
| Browser (session token) | nothing: no `/v1/session/*` route accepts modules or a locale |

## Security notes

- Free-text parameters (strings without `enum`) are the only values a session can give that are not chosen from a list,
  so they pass the guard's input checks at mint, as a person's message would (`input.size`, `input.encoding`,
  `input.injection`, moderation, scope). With a control in `enforce` mode the mint is refused (400
  `module_param_rejected`); in `monitor` mode the token is minted and a security event is recorded. What the guard
  cleans (invisible characters with `input.encoding` enforced) is stored cleaned. Server runs check the values the
  same way. Events keep the control and the module and parameter names, never the value.
- Free-text values also go through `pii.model` before the prompt reaches the model, like page text: with the control
  enforced, personal data in them is tokenised or masked.
- Prefer choices and a data table to free text: a session that picks `style: detailed` cannot write anything into the
  prompt.
- Traces record which modules were rendered, as `prompt.modules: ["tone@2", ...]`; parameter values are never traced.
- The console's **Prompts** page builds schemas visually (or as raw JSON), edits the data table, shows the version
  history with a diff, and previews a draft with sample values through `POST /v1/admin/prompts/{name}/preview`, which
  runs the same checks as a save.
